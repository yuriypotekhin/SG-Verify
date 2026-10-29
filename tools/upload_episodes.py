#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Upload CVAT packages (output of to_cvat.py) to a CVAT server, one task per episode.

For every package folder: create the task (named after the folder = episode id, optionally inside a
project) -> upload frames.zip -> import annotations.xml as "CVAT 1.1" with masks kept as masks ->
attach guide.md as the task guide. A task whose name already exists (in the project, or on the server
without --project) is skipped, so the script can be re-run after an interruption. A task that fails half-way
is deleted again (unless --keep-failed), so a re-run retries it.

With --project NAME the project is created if it does not exist, with the union of the labels of all
packages being uploaded; if it exists, labels (roles, and role values of the relation / action
attributes) that the packages need and the project lacks are added to it.

With --packages-dir DIR every frames.zip is also copied to DIR/<task name>/frames.zip: the SAM tracking
service of SG-Verify reads the frames from there.

Connection: environment variables CVAT_URL (default http://localhost:8080), CVAT_USER, CVAT_PASSWORD and
optionally CVAT_HOST_HEADER (see cvat_api.py).

Usage::

    upload_episodes.py PACKAGES [--project NAME] [--packages-dir DIR] [--only NAMES_FILE]

PACKAGES is a folder of package folders (or one package folder). NAMES_FILE lists episode names, one
per line (# starts a comment).
"""
import argparse
import copy
import json
import os
import os.path as osp
import shutil
import sys
import time
import xml.etree.ElementTree as ET
import zipfile

sys.path.insert(0, osp.dirname(osp.abspath(__file__)))
from cvat_api import Cvat, CvatError  # noqa: E402
from sg_format import IMAGE_EXTS, natural_key, parse_sg_meta  # noqa: E402

PACKAGE_FILES = ("frames.zip", "labels.json", "annotations.xml", "guide.md")


def find_packages(root):
    if osp.isfile(osp.join(root, "annotations.xml")):
        return [osp.normpath(root)]
    return sorted((osp.join(root, d) for d in os.listdir(root) if osp.isdir(osp.join(root, d))
                   and osp.isfile(osp.join(root, d, "annotations.xml"))), key=natural_key)


def read_names(path):
    with open(path, encoding="utf-8") as f:
        return {ln.split("#", 1)[0].strip() for ln in f if ln.split("#", 1)[0].strip()}


def load_labels(pkg):
    with open(osp.join(pkg, "labels.json"), encoding="utf-8") as f:
        return json.load(f)


# ------------------------------------------------------------------------------------------- labels

def union_labels(specs):
    """Merge several labels.json lists: labels by name in first-seen order, select values united."""
    out, by_name = [], {}
    for spec in specs:
        for lab in spec:
            have = by_name.get(lab["name"])
            if have is None:
                have = by_name[lab["name"]] = copy.deepcopy(lab)
                out.append(have)
                continue
            attrs = {a["name"]: a for a in have.get("attributes", [])}
            for at in lab.get("attributes", []):
                if at["name"] not in attrs:
                    have.setdefault("attributes", []).append(copy.deepcopy(at))
                elif at.get("input_type") == "select":
                    vals = attrs[at["name"]]["values"]
                    vals.extend(v for v in at["values"] if v not in vals)
    # role labels first, tag labels last (as to_cvat.py writes them)
    return [l for l in out if l.get("type") != "tag"] + [l for l in out if l.get("type") == "tag"]


def label_changes(wanted, existing):
    """PATCH payload that adds to the existing project labels what `wanted` needs; [] if nothing is missing."""
    by_name = {l["name"]: l for l in existing}
    keys = ("id", "name", "input_type", "mutable", "default_value", "values")
    patch = []
    for lab in wanted:
        ex = by_name.get(lab["name"])
        if ex is None:
            patch.append(lab)
            continue
        ex_attrs = {a["name"]: a for a in ex.get("attributes", [])}
        changed, attrs = False, []
        for a in ex.get("attributes", []):
            attrs.append({k: a[k] for k in keys if k in a})
        for at in lab.get("attributes", []):
            ea = ex_attrs.get(at["name"])
            if ea is None:
                attrs.append(at)
                changed = True
            elif at.get("input_type") == "select":
                missing = [v for v in at["values"] if v not in ea["values"]]
                if missing:
                    for x in attrs:
                        if x["name"] == at["name"]:
                            x["values"] = list(ea["values"]) + missing
                    changed = True
        if changed:
            patch.append({"id": ex["id"], "name": ex["name"], "attributes": attrs})
    return patch


def ensure_project(api, key, packages):
    wanted = union_labels([load_labels(p) for p in packages])
    proj = api.find_project(key)
    if proj is None:
        r = api.post("/api/projects", json={"name": key, "labels": wanted}).json()
        print("created project %r (id %d) with %d labels" % (key, r["id"], len(wanted)), flush=True)
        return r["id"]
    pid = proj["id"]
    patch = label_changes(wanted, api.labels(project_id=pid))
    if patch:
        print("project %r (id %d): adding missing labels / values: %s"
              % (proj["name"], pid, ", ".join(l["name"] for l in patch)), flush=True)
        api.request("PATCH", "/api/projects/%d" % pid, json={"labels": patch})
        still = label_changes(wanted, api.labels(project_id=pid))
        if still:
            raise CvatError("could not add labels %s to project %d; create a new project instead"
                            % ([l["name"] for l in still], pid))
    else:
        print("project %r (id %d): labels are complete" % (proj["name"], pid), flush=True)
    return pid


# ------------------------------------------------------------------------------------------- upload

def xml_counts(path):
    """(tracks, masks, tags) in a package's annotations.xml."""
    root = ET.parse(path).getroot()
    return len(root.findall("track")), len(root.findall("image/mask")), len(root.findall("image/tag"))


def copy_frames(pkg, name, packages_dir):
    src = osp.join(pkg, "frames.zip")
    dst_dir = osp.join(packages_dir, name)
    dst = osp.join(dst_dir, "frames.zip")
    if osp.exists(dst) and (osp.samefile(src, dst) or osp.getsize(dst) == osp.getsize(src)):
        return False
    os.makedirs(dst_dir, exist_ok=True)
    shutil.copyfile(src, dst + ".tmp")
    os.replace(dst + ".tmp", dst)
    return True


def upload_one(api, pkg, name, project_id, a):
    spec = {"name": name}
    if project_id is not None:   # a task in a project takes the project labels; it may not send its own
        spec["project_id"] = project_id
    else:
        spec["labels"] = load_labels(pkg)
    tid = api.post("/api/tasks", json=spec).json()["id"]
    try:
        zpath = osp.join(pkg, "frames.zip")
        with zipfile.ZipFile(zpath) as z:
            n = sum(1 for m in z.namelist() if m.lower().endswith(IMAGE_EXTS))
        print("    task %d: uploading %d frames (%.1f MB)" % (tid, n, osp.getsize(zpath) / 1e6), flush=True)
        with open(zpath, "rb") as f:
            r = api.post("/api/tasks/%d/data" % tid, timeout=a.timeout,
                         data={"image_quality": a.image_quality, "use_zip_chunks": "true", "use_cache": "true",
                               "sorting_method": "natural"},
                         files={"client_files[0]": ("frames.zip", f, "application/zip")})
        api.wait(r.json()["rq_id"], "frame upload", a.timeout)
        print("    task %d: importing annotations" % tid, flush=True)
        with open(osp.join(pkg, "annotations.xml"), "rb") as f:
            r = api.post("/api/tasks/%d/annotations" % tid, timeout=a.timeout,
                         params={"format": "CVAT 1.1", "conv_mask_to_poly": "false"},
                         files={"annotation_file": ("annotations.xml", f, "text/xml")})
        api.wait(r.json()["rq_id"], "annotation import", a.timeout)
        with open(osp.join(pkg, "guide.md"), encoding="utf-8") as f:
            api.post("/api/guides", json={"task_id": tid, "markdown": f.read()})
        ann = api.get("/api/tasks/%d/annotations" % tid)
        return tid, n, ann
    except Exception:
        if not a.keep_failed:
            try:
                api.request("DELETE", "/api/tasks/%d" % tid)
                print("    task %d: deleted after the error (use --keep-failed to keep it)" % tid, flush=True)
            except CvatError as e:
                print("    task %d: could not delete it: %s" % (tid, e), flush=True)
        raise


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter,
                                 epilog="Environment: CVAT_URL, CVAT_USER, CVAT_PASSWORD, CVAT_HOST_HEADER")
    ap.add_argument("packages", help="folder of package folders made by to_cvat.py (or one package folder)")
    ap.add_argument("--project", help="project name (or id) to put the tasks into; created if missing")
    ap.add_argument("--packages-dir", help="also copy each frames.zip to DIR/<task name>/frames.zip "
                                           "(the folder the SAM tracking service reads)")
    ap.add_argument("--only", help="file with the episode names to upload, one per line")
    ap.add_argument("--image-quality", type=int, default=95, help="CVAT image quality of compressed chunks")
    ap.add_argument("--timeout", type=int, default=3600, help="seconds to wait for one upload / import")
    ap.add_argument("--keep-failed", action="store_true", help="do not delete a task whose upload failed")
    a = ap.parse_args()

    if not osp.isdir(a.packages):
        ap.error("%s is not a folder" % a.packages)
    pkgs = find_packages(a.packages)
    if a.only:
        only = read_names(a.only)
        pkgs = [p for p in pkgs if osp.basename(p) in only]
        missing = only - {osp.basename(p) for p in pkgs}
        if missing:
            print("warning: no package for %d listed name(s): %s" % (len(missing), ", ".join(sorted(missing))))
    bad = [p for p in pkgs if not all(osp.isfile(osp.join(p, f)) for f in PACKAGE_FILES)]
    for p in bad:
        print("skip %s: package incomplete (needs %s)" % (osp.basename(p), ", ".join(PACKAGE_FILES)))
    pkgs = [p for p in pkgs if p not in bad]
    if not pkgs:
        sys.exit("error: no complete packages found in %s" % a.packages)

    api = Cvat.from_env()
    try:
        me = api.whoami()
        print("CVAT %s, user %s; %d package(s)" % (api.url, me.get("username"), len(pkgs)), flush=True)
        pid = ensure_project(api, a.project, pkgs) if a.project else None
        have = {t["name"]: t for t in api.tasks(pid)}
    except CvatError as e:
        sys.exit("error: %s" % e)

    stats = {"created": 0, "skipped": 0, "failed": 0}
    for j, pkg in enumerate(pkgs, 1):
        name = osp.basename(pkg)
        head = "[%d/%d] %s" % (j, len(pkgs), name)
        with open(osp.join(pkg, "guide.md"), encoding="utf-8") as f:
            ep = parse_sg_meta(f.read()).get("episode")
        if ep and ep != name:
            print("%s: warning: guide says episode %r; the SAM service looks frames up by it" % (head, ep))
        if a.packages_dir and copy_frames(pkg, name, a.packages_dir):
            print("%s: frames.zip copied to %s" % (head, osp.join(a.packages_dir, name)), flush=True)
        if name in have:
            t = have[name]
            note = "" if t.get("size") else " -- WARNING: it has no frames; delete it in CVAT and re-run"
            print("%s: exists, task %d%s" % (head, t["id"], note), flush=True)
            stats["skipped"] += 1
            continue
        t0 = time.time()
        print("%s: creating task" % head, flush=True)
        try:
            tid, n, ann = upload_one(api, pkg, name, pid, a)
        except Exception as e:   # one broken package must not stop the batch
            print("%s: FAILED: %s: %s" % (head, type(e).__name__, e), flush=True)
            stats["failed"] += 1
            continue
        got = (len(ann.get("tracks", [])), sum(1 for s in ann.get("shapes", []) if s.get("type") == "mask"),
               len(ann.get("tags", [])))
        want = xml_counts(osp.join(pkg, "annotations.xml"))
        check = "" if got == want else " -- WARNING: the package has %d tracks, %d masks, %d tags" % want
        print("%s: done, task %d: %d frames, %d tracks, %d masks, %d tags, %.0f s%s"
              % ((head, tid, n) + got + (time.time() - t0, check)), flush=True)
        stats["created"] += 1
    print("created %(created)d, already there %(skipped)d, failed %(failed)d" % stats)
    sys.exit(1 if stats["failed"] else 0)


if __name__ == "__main__":
    main()
