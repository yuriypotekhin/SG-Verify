#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Minimal CVAT REST client shared by upload_episodes.py and export_episodes.py (not a command-line tool).

Connection settings come from the environment:

    CVAT_URL           server address (default http://localhost:8080)
    CVAT_USER          user name (required)
    CVAT_PASSWORD      password (required)
    CVAT_HOST_HEADER   optional Host header, e.g. "localhost" when CVAT sits behind a reverse proxy that
                       routes by host name and you reach it through another address (an ssh tunnel, an IP)

Authentication is HTTP Basic, as accepted by the CVAT API.
"""
import os
import sys
import time

import requests


class CvatError(RuntimeError):
    pass


class Cvat:
    def __init__(self, url, user, password, host_header=None, timeout=120):
        self.url = url.rstrip("/")
        self.user = user
        self.timeout = timeout
        self.s = requests.Session()
        self.s.auth = (user, password)
        if host_header:
            self.s.headers["Host"] = host_header

    @classmethod
    def from_env(cls):
        url = os.environ.get("CVAT_URL", "http://localhost:8080")
        user, password = os.environ.get("CVAT_USER"), os.environ.get("CVAT_PASSWORD")
        missing = [k for k, v in (("CVAT_USER", user), ("CVAT_PASSWORD", password)) if not v]
        if missing:
            sys.exit("error: set %s in the environment, e.g.\n"
                     "  export CVAT_URL=http://localhost:8080 CVAT_USER=<user>\n"
                     "  read -s CVAT_PASSWORD && export CVAT_PASSWORD" % " and ".join(missing))
        return cls(url, user, password, os.environ.get("CVAT_HOST_HEADER") or None)

    # ---------------------------------------------------------------------------------------- HTTP

    def _abs(self, path):
        if path.startswith("http://") or path.startswith("https://"):
            # the server writes its own external address into pagination links; keep ours
            return self.url + "/api/" + path.split("/api/", 1)[1] if "/api/" in path else path
        return self.url + path

    def request(self, method, path, ok=(200, 201, 202, 204), **kw):
        kw.setdefault("timeout", self.timeout)
        try:
            r = self.s.request(method, self._abs(path), **kw)
        except (requests.ConnectionError, requests.Timeout) as e:
            raise CvatError("cannot reach CVAT at %s (%s)" % (self.url, e.__class__.__name__))
        if r.status_code == 401:
            raise CvatError("CVAT rejected the login of user %r at %s: check CVAT_USER / CVAT_PASSWORD"
                            % (self.user, self.url))
        if r.status_code not in ok:
            raise CvatError("%s %s: HTTP %d %s" % (method, path, r.status_code, r.text[:500]))
        return r

    def get(self, path, **params):
        return self.request("GET", path, params=params or None).json()

    def post(self, path, **kw):
        return self.request("POST", path, **kw)

    def pages(self, path, **params):
        """All items of a paginated list endpoint."""
        params.setdefault("page_size", 100)
        r = self.get(path, **params)
        while True:
            for item in r.get("results", []):
                yield item
            nxt = r.get("next")
            if not nxt:
                return
            r = self.request("GET", nxt).json()

    def wait(self, rq_id, what, timeout=3600):
        """Wait for a background request (/api/requests/<id>) to finish."""
        t0, delay = time.time(), 0.5
        while time.time() - t0 < timeout:
            r = self.get("/api/requests/%s" % rq_id)
            status = r.get("status")
            if status == "finished":
                return r
            if status == "failed":
                raise CvatError("%s failed on the server: %s" % (what, r.get("message")))
            time.sleep(delay)
            delay = min(delay * 1.5, 5.0)
        raise CvatError("%s: no result after %d s" % (what, timeout))

    # ----------------------------------------------------------------------------------- helpers

    def whoami(self):
        return self.get("/api/users/self")

    def find_project(self, key):
        """Project dict by exact name, or by numeric id; None if not found."""
        projects = list(self.pages("/api/projects"))
        for p in projects:
            if p["name"] == key:
                return p
        if str(key).isdigit():
            for p in projects:
                if p["id"] == int(key):
                    return p
        return None

    def labels(self, project_id=None, task_id=None):
        params = {"project_id": project_id} if project_id is not None else {"task_id": task_id}
        return list(self.pages("/api/labels", **params))

    def tasks(self, project_id=None):
        params = {"project_id": project_id} if project_id is not None else {}
        return list(self.pages("/api/tasks", **params))
