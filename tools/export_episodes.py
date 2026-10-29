#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Export the reviewed episodes of a CVAT project back to the SG-Verify episode format.

For every task of the project (task name = episode id)::

    OUT/<episode>/masks/<role>/frame_%06d.png   0/255 mask per object and frame (empty frames are all zero,
                                                or not written with --sparse-masks)
    OUT/<episode>/graph.json                    objects (id = role, roles, name, level, boxes per frame) and
                                                edges (type, subject, predicate, object, frames, intervals)
    OUT/<episode>/meta.json                     episode, goal, verdict, reviewer, n_frames, frame_size, ...
    OUT/<episode>/frames.zip | frames/          only with --packages-dir / --download-frames

The output is valid input for to_cvat.py (frames have to be present for that: use one of the two frame
options, or put the frames back yourself).

Verdict = the task's subset as set by the review panel (``verified``, ``goal_mismatch`` or empty if not
reviewed); reviewer = the task assignee. Objects joined in the panel ("Unite objects", sg-meta ``merges``)
become one object with several roles.

Connection: environment variables CVAT_URL (default http://localhost:8080), CVAT_USER, CVAT_PASSWORD and
optionally CVAT_HOST_HEADER (see cvat_api.py).

Usage::

    export_episodes.py PROJECT OUT [--only NAMES_FILE] [--packages-dir DIR | --download-frames]
                       [--sparse-masks] [--raw]

PROJECT is a project name or id.
"""
import argparse
import collections
import gzip
import io
import json
import os
import os.path as osp
import shutil
import sys
import time

import numpy as np
from PIL import Image

sys.path.insert(0, osp.dirname(osp.abspath(__file__)))
from sg_format import (NONE, TAG_FIELDS, frames_to_intervals, is_none, natural_key,  # noqa: E402
                       parse_sg_meta, rle_decode)

CORE_META_KEYS = ("episode", "goal", "entities", "ontology", "merges")


# ------------------------------------------------------------------------------- pure conversion

def resolve_merges(merges):
    """{role: role it was merged into} with chains followed to the end: {a: b, b: c} -> {a: c, b: c}."""
    out = {}
    for r in merges or {}:
        seen, x = {r}, merges[r]
        while x in merges and x not in seen:
            seen.add(x)
            x = merges[x]
        if x != r:
            out[r] = x
    return out


def per_frame(shapes, n, interpolate):
    """Track shapes (keyframes) -> {frame: points} for frames 0..n-1, following CVAT track semantics.

    A keyframe holds until the next one; an ``outside`` keyframe hides the object until the next keyframe.
    Rectangles are interpolated linearly between two visible keyframes, masks are not.
    """
    kf = sorted(shapes, key=lambda s: s["frame"])
    out = {}
    for j, a in enumerate(kf):
        if a.get("outside"):
            continue
        b = kf[j + 1] if j + 1 < len(kf) else None
        end = min(b["frame"] if b is not None else n, n)
        for f in range(max(a["frame"], 0), end):
            if interpolate and b is not None and not b.get("outside") and f != a["frame"]:
                w = (f - a["frame"]) / float(b["frame"] - a["frame"])
                out[f] = [pa + (pb - pa) * w for pa, pb in zip(a["points"], b["points"])]
            else:
                out[f] = list(a["points"])
    return out


def episode_from_cvat(task, labels, ann, guide, frames_meta):
    """Convert one CVAT task to the episode format (no I/O).

    task: GET /api/tasks/<id>; labels: GET /api/labels?project_id=...; ann: GET /api/tasks/<id>/annotations;
    guide: task guide markdown; frames_meta: GET /api/tasks/<id>/data/meta.
    Returns {"graph", "meta", "masks": {(role, frame): [mask points, ...]}, "n", "sizes", "warnings"}.
    """
    warnings = []
    lab = {l["id"]: l["name"] for l in labels}
    order = {l["name"]: i for i, l in enumerate(labels)}
    spec = {a["id"]: a["name"] for l in labels for a in l.get("attributes", [])}
    defaults = {l["name"]: {a["name"]: a.get("default_value", "") for a in l.get("attributes", [])} for l in labels}
    tag_labels = {l["name"] for l in labels if l.get("type") == "tag"} | set(TAG_FIELDS)

    def attrs(obj, label_name=None):
        out = dict(defaults.get(label_name, {})) if label_name else {}
        for at in obj.get("attributes", []):
            if at.get("spec_id") in spec:
                out[spec[at["spec_id"]]] = at.get("value", "")
        return out

    sg = parse_sg_meta(guide)
    merges = resolve_merges(sg.get("merges") or {})
    alias = lambda r: merges.get(r, r)  # noqa: E731
    n = int(task.get("size") or 0)
    frames = frames_meta.get("frames") or []
    if not frames:
        raise ValueError("task %s has no frame metadata" % task.get("name"))
    sizes = [(int(frames[min(f, len(frames) - 1)]["width"]), int(frames[min(f, len(frames) - 1)]["height"]))
             for f in range(n)]

    names = collections.defaultdict(collections.Counter)   # role -> name votes of its own label
    names_merged = collections.defaultdict(collections.Counter)   # votes of labels merged into it
    boxes = collections.defaultdict(dict)                   # role -> {frame: [x0, y0, x1, y1]}
    mask_by_label = {}                                      # (label name, frame) -> shape (latest id wins)
    dup = 0
    present = set()

    def vote(label_name, obj):
        v = str(attrs(obj).get("name") or "").strip()
        if v:
            (names if label_name not in merges else names_merged)[alias(label_name)][v] += 1

    for sh in ann.get("shapes", []):
        name = lab.get(sh["label_id"])
        if name is None or name in tag_labels or not 0 <= sh["frame"] < n:
            continue
        role = alias(name)
        present.add(role)
        vote(name, sh)
        if sh["type"] == "mask":
            k = (name, sh["frame"])
            if k in mask_by_label:
                dup += 1
                if sh.get("id", 0) < mask_by_label[k].get("id", 0):
                    continue
            mask_by_label[k] = sh
        elif sh["type"] == "rectangle":
            f, p = sh["frame"], [float(v) for v in sh["points"][:4]]
            old = boxes[role].get(f)
            boxes[role][f] = p if old is None else [min(old[0], p[0]), min(old[1], p[1]),
                                                     max(old[2], p[2]), max(old[3], p[3])]
    for tr in ann.get("tracks", []):
        name = lab.get(tr["label_id"])
        if name is None or name in tag_labels:
            continue
        role = alias(name)
        present.add(role)
        vote(name, tr)
        for s in tr.get("shapes", []):
            vote(name, s)
        rects = [s for s in tr.get("shapes", []) if s.get("type", "rectangle") == "rectangle"]
        for f, p in per_frame(rects, n, True).items():
            old = boxes[role].get(f)
            p = [float(v) for v in p[:4]]
            boxes[role][f] = p if old is None else [min(old[0], p[0]), min(old[1], p[1]),
                                                     max(old[2], p[2]), max(old[3], p[3])]
        mshapes = [s for s in tr.get("shapes", []) if s.get("type") == "mask"]
        for f, p in per_frame(mshapes, n, False).items():
            mask_by_label.setdefault((name, f), {"points": p, "id": -1})
    if dup:
        warnings.append("%d duplicate mask(s) of one label on one frame: kept the latest" % dup)

    masks = collections.defaultdict(list)
    for (name, f), sh in mask_by_label.items():
        masks[(alias(name), f)].append(sh["points"])

    # objects: guide entities first (in their order), then other annotated labels (e.g. spare roles)
    entities = [e for e in sg.get("entities", []) if e.get("role") and e["role"] not in merges]
    ent = {e["role"]: e for e in entities}
    roles = [e["role"] for e in entities]
    roles += sorted((r for r in present if r not in ent), key=lambda r: (order.get(r, 1e9), natural_key(r)))
    objects = []
    for r in roles:
        e = ent.get(r, {})
        votes = names[r] or names_merged[r]
        name = votes.most_common(1)[0][0] if votes else str(e.get("name") or r)
        if len(votes) > 1:
            warnings.append("%s: several names %s, kept %r" % (r, dict(votes), name))
        o = {"id": r, "roles": [r] + sorted((k for k, v in merges.items() if v == r), key=natural_key),
             "name": name}
        if isinstance(e.get("level"), (int, float)) and not isinstance(e.get("level"), bool):
            o["level"] = int(e["level"])
        if e.get("entity_id") not in (None, "", r):
            o["entity_id"] = str(e["entity_id"])
        if boxes.get(r):
            o["boxes"] = {str(f): [round(v, 2) for v in b] for f, b in sorted(boxes[r].items())}
        objects.append(o)

    # edges: tags grouped by (type, subject, predicate, object)
    groups = collections.defaultdict(set)
    skipped = 0
    for tg in ann.get("tags", []):
        kind = lab.get(tg["label_id"])
        if kind not in TAG_FIELDS or not 0 <= tg["frame"] < n:
            continue
        a = attrs(tg, kind)
        fs, fp, fo = TAG_FIELDS[kind]
        src, pred, dst = str(a.get(fs) or "").strip(), str(a.get(fp) or "").strip(), a.get(fo)
        if not src or not pred:
            skipped += 1
            continue
        src = alias(src)
        dst = None if is_none(dst) else alias(str(dst))
        if dst == src:
            dst = None
        groups[(kind, src, pred, dst)].add(tg["frame"])
    if skipped:
        warnings.append("%d tag(s) without subject or predicate skipped" % skipped)
    edges = [{"type": k[0], "subject": k[1], "predicate": k[2], "object": k[3], "frames": sorted(v),
              "intervals": frames_to_intervals(v)}
             for k, v in sorted(groups.items(), key=lambda kv: (min(kv[1]), kv[0][0], kv[0][1], kv[0][2],
                                                                kv[0][3] or ""))]

    assignee = task.get("assignee") or {}
    meta = {"episode": task["name"], "goal": sg.get("goal", ""), "verdict": task.get("subset") or "",
            "reviewer": assignee.get("username", "") if isinstance(assignee, dict) else "",
            "n_frames": n, "frame_size": list(sizes[0]) if sizes else None,
            "n_objects": len(objects), "n_edges": len(edges),
            "ontology": sg.get("ontology") or {"states": [], "actions": []},
            "cvat": {"task_id": task.get("id"), "project_id": task.get("project_id"),
                     "updated_date": task.get("updated_date")}}
    extra = {k: v for k, v in sg.items() if k not in CORE_META_KEYS}
    if extra:
        meta["sg_meta_extra"] = extra
    return {"graph": {"objects": objects, "edges": edges}, "meta": meta, "masks": dict(masks), "n": n,
            "sizes": sizes, "warnings": warnings}


def write_episode(d, ep, sparse=False):
    """Write masks/, graph.json and meta.json of one converted episode into folder d."""
    os.makedirs(d, exist_ok=True)
    mroot = osp.join(d, "masks")
    if osp.isdir(mroot):            # stale roles of an earlier export must not survive
        shutil.rmtree(mroot)
    n_files = n_nonempty = 0
    for o in ep["graph"]["objects"]:
        role = o["id"]
        md = osp.join(mroot, role)
        os.makedirs(md, exist_ok=True)
        for f in range(ep["n"]):
            w, h = ep["sizes"][f]
            pts = ep["masks"].get((role, f))
            if not pts and sparse:
                continue
            m = np.zeros((h, w), np.uint8)
            for p in pts or []:
                m |= rle_decode(p, w, h)
            Image.fromarray(m.astype(np.uint8) * 255).save(osp.join(md, "frame_%06d.png" % f))
            n_files += 1
            n_nonempty += int(bool(pts))
    for fn, obj in (("graph.json", ep["graph"]), ("meta.json", ep["meta"])):
        with open(osp.join(d, fn), "w", encoding="utf-8") as f:
            json.dump(obj, f, ensure_ascii=False, indent=1)
    return n_files, n_nonempty


# -------------------------------------------------------------------------------------------- I/O

def image_ext(data):
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return ".png"
    if data[:2] == b"\xff\xd8":
        return ".jpg"
    fmt = Image.open(io.BytesIO(data)).format or "png"
    return "." + fmt.lower().replace("jpeg", "jpg")


def download_frames(api, task_id, n, d):
    fd = osp.join(d, "frames")
    os.makedirs(fd, exist_ok=True)
    for f in range(n):
        r = api.request("GET", "/api/tasks/%d/data" % task_id,
                        params={"type": "frame", "number": f, "quality": "original"})
        with open(osp.join(fd, "frame_%06d%s" % (f, image_ext(r.content))), "wb") as out:
            out.write(r.content)


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter,
                                 epilog="Environment: CVAT_URL, CVAT_USER, CVAT_PASSWORD, CVAT_HOST_HEADER")
    ap.add_argument("project", help="CVAT project name or id")
    ap.add_argument("out", help="output folder; each episode goes to OUT/<episode>/")
    ap.add_argument("--only", help="file with the episode (task) names to export, one per line")
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--packages-dir", help="copy DIR/<episode>/frames.zip into each exported episode")
    g.add_argument("--download-frames", action="store_true", help="download the original frames from CVAT")
    ap.add_argument("--sparse-masks", action="store_true", help="write mask files only for non-empty masks")
    ap.add_argument("--raw", action="store_true",
                    help="also save the raw CVAT task, guide and annotations to OUT/_raw/<episode>.json.gz")
    a = ap.parse_args()

    from cvat_api import Cvat, CvatError   # needs requests; the conversion above does not
    api = Cvat.from_env()
    try:
        proj = api.find_project(a.project)
        if proj is None:
            sys.exit("error: no project %r on %s" % (a.project, api.url))
        pid = proj["id"]
        labels = api.labels(project_id=pid)
        tasks = sorted(api.tasks(pid), key=lambda t: natural_key(t["name"]))
    except CvatError as e:
        sys.exit("error: %s" % e)
    if a.only:
        with open(a.only, encoding="utf-8") as f:
            only = {ln.split("#", 1)[0].strip() for ln in f if ln.split("#", 1)[0].strip()}
        tasks = [t for t in tasks if t["name"] in only]
    print("project %r (id %d): exporting %d task(s) -> %s" % (proj["name"], pid, len(tasks), a.out), flush=True)
    os.makedirs(a.out, exist_ok=True)

    verdicts, failed = collections.Counter(), 0
    for j, t in enumerate(tasks, 1):
        head = "[%d/%d] %s" % (j, len(tasks), t["name"])
        if not t.get("size"):
            print("%s: no frames, skipped" % head, flush=True)
            continue
        t0 = time.time()
        try:
            ann = api.get("/api/tasks/%d/annotations" % t["id"])
            guide = api.get("/api/guides/%d" % t["guide_id"])["markdown"] if t.get("guide_id") else ""
            fmeta = api.get("/api/tasks/%d/data/meta" % t["id"])
            if a.raw:
                rd = osp.join(a.out, "_raw")
                os.makedirs(rd, exist_ok=True)
                with gzip.open(osp.join(rd, t["name"] + ".json.gz"), "wt", encoding="utf-8") as f:
                    json.dump({"task": t, "guide": guide, "annotations": ann, "data_meta": fmeta}, f,
                              ensure_ascii=False)
            ep = episode_from_cvat(t, labels, ann, guide, fmeta)
            d = osp.join(a.out, t["name"])
            n_files, n_nonempty = write_episode(d, ep, a.sparse_masks)
            if a.packages_dir:
                src = osp.join(a.packages_dir, t["name"], "frames.zip")
                if osp.isfile(src):
                    shutil.copyfile(src, osp.join(d, "frames.zip"))
                else:
                    ep["warnings"].append("no %s" % src)
            elif a.download_frames:
                download_frames(api, t["id"], ep["n"], d)
        except Exception as e:   # one broken task must not stop the export
            print("%s: FAILED: %s: %s" % (head, type(e).__name__, e), flush=True)
            failed += 1
            continue
        m = ep["meta"]
        verdicts[m["verdict"] or "(none)"] += 1
        print("%s: verdict %s%s, %d frames, %d objects, %d non-empty masks, %d edges, %.1f s"
              % (head, m["verdict"] or "-", " by " + m["reviewer"] if m["reviewer"] else "", ep["n"],
                 m["n_objects"], n_nonempty, m["n_edges"], time.time() - t0), flush=True)
        for w in ep["warnings"]:
            print("    warning: %s" % w, flush=True)
    print("exported %d, failed %d; verdicts: %s" % (sum(verdicts.values()), failed, dict(verdicts)))
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
