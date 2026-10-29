#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Convert SG-Verify episode folders into CVAT packages (one CVAT task per episode).

Input episode folder (see docs/DATA_FORMAT.md)::

    <episode>/frames/*.png|*.jpg          frames in natural order (or frames.zip, or a video file)
    <episode>/masks/<object>/frame_%06d.png   optional binary masks (non-zero = object)
    <episode>/graph.json                  {"objects": [...], "edges": [...]}
    <episode>/meta.json                   {"goal": "..."} (optional)

Output package (what upload_episodes.py sends to CVAT)::

    <out>/<episode>/frames.zip        frames renamed frame_000000.<ext>, frame_000001.<ext>, ...
    <out>/<episode>/labels.json       task labels: object roles (+ spare roles), relation and action tags
    <out>/<episode>/annotations.xml   "CVAT 1.1" XML: a box track per object, a mask per object and frame,
                                      edges as frame tags
    <out>/<episode>/guide.md          task guide: goal, role -> object table, checklist, sg-meta block

Usage::

    to_cvat.py EPISODE_DIR OUT_DIR [options]          # one episode
    to_cvat.py EPISODES_ROOT OUT_DIR --all [options]  # every sub-folder that has a graph.json

Options: --ontology FILE (states/actions suggested in the panel; default: collected from the edges),
--spare N (empty extra roles extra1..N, default 2), --roles-from auto|id|index, --video FILE
(single episode; every frame is extracted with ffmpeg), --video-format jpg|png, --checklist FILE,
--skip-existing, --workers N. Frame numbers in masks, boxes and edges count from 0 in the order of the
frames (natural sort of the file names, or decoding order of the video).
"""
import argparse
import io
import json
import os
import os.path as osp
import shutil
import subprocess
import sys
import tempfile
import time
import zipfile
from concurrent.futures import ProcessPoolExecutor
from xml.sax.saxutils import escape, quoteattr

import numpy as np
from PIL import Image

sys.path.insert(0, osp.dirname(osp.abspath(__file__)))
from sg_format import (IMAGE_EXTS, NONE, RESERVED_LABELS, VIDEO_EXTS, edge_frames, guide_md,  # noqa: E402
                       is_none, labels_json, load_mask, mask_bbox, mask_to_cvat, natural_key)


class EpisodeError(Exception):
    pass


def log_warn(ep, msg):
    print("  warning [%s]: %s" % (ep, msg), file=sys.stderr, flush=True)


# ---------------------------------------------------------------------------------------------- frames

def extract_video(video, dst, fmt="jpg"):
    """Extract every frame of a video with ffmpeg into dst/frame_%06d.<fmt>; returns sorted file paths."""
    if shutil.which("ffmpeg") is None:
        raise EpisodeError("ffmpeg is not installed; extract the frames into frames/ yourself")
    pattern = osp.join(dst, "frame_%06d." + fmt)
    base = ["ffmpeg", "-v", "error", "-y", "-i", video]
    tail = (["-q:v", "2"] if fmt == "jpg" else []) + ["-start_number", "0", pattern]
    # keep every decoded frame as is; -fps_mode is the new spelling, -vsync 0 the old one
    for sync in (["-fps_mode", "passthrough"], ["-vsync", "0"]):
        r = subprocess.run(base + sync + tail, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        if r.returncode == 0:
            break
    else:
        raise EpisodeError("ffmpeg failed on %s: %s" % (video, r.stderr.decode(errors="replace")[-500:]))
    return sorted((osp.join(dst, f) for f in os.listdir(dst)), key=natural_key)


def find_video(ep_dir):
    vids = sorted(f for f in os.listdir(ep_dir) if f.lower().endswith(VIDEO_EXTS))
    if not vids:
        return None
    for f in vids:
        if osp.splitext(f)[0].lower() == "video":
            return osp.join(ep_dir, f)
    return osp.join(ep_dir, vids[0])


def write_frames_zip(ep_dir, out_dir, video=None, video_format="jpg"):
    """Write <out_dir>/frames.zip. Returns (zip names, original stems, width, height).

    Frame source, first found: --video, <ep>/frames/, <ep>/frames.zip, a video file in <ep>/.
    """
    frames_dir = osp.join(ep_dir, "frames")
    src_zip = osp.join(ep_dir, "frames.zip")
    tmp_dir = None
    items = []   # (zip name, original stem, path or None, zip member or None)
    if not video and osp.isdir(frames_dir):
        files = sorted((f for f in os.listdir(frames_dir) if f.lower().endswith(IMAGE_EXTS)), key=natural_key)
        items = [(None, osp.splitext(f)[0], osp.join(frames_dir, f), None) for f in files]
    elif not video and osp.isfile(src_zip):
        with zipfile.ZipFile(src_zip) as z:
            members = sorted((m for m in z.namelist() if m.lower().endswith(IMAGE_EXTS)), key=natural_key)
        items = [(None, osp.splitext(osp.basename(m))[0], None, m) for m in members]
    else:
        video = video or find_video(ep_dir)
        if not video:
            raise EpisodeError("no frames/ folder, frames.zip or video file")
        tmp_dir = tempfile.mkdtemp(prefix="frames_", dir=out_dir)
        files = extract_video(video, tmp_dir, video_format)
        items = [(None, osp.splitext(osp.basename(f))[0], f, None) for f in files]
    if not items:
        raise EpisodeError("no frames found")

    names, stems, sizes = [], [], set()
    tmp = osp.join(out_dir, "frames.zip.tmp")
    try:
        zsrc = zipfile.ZipFile(src_zip) if items[0][3] is not None else None
        with zipfile.ZipFile(tmp, "w", zipfile.ZIP_STORED) as z:
            for i, (_, stem, path, member) in enumerate(items):
                ext = osp.splitext(path or member)[1].lower()
                ext = ".jpg" if ext == ".jpeg" else ext
                name = "frame_%06d%s" % (i, ext)
                if path is not None:
                    with Image.open(path) as im:
                        sizes.add(im.size)
                    z.write(path, name)
                else:
                    data = zsrc.read(member)
                    with Image.open(io.BytesIO(data)) as im:
                        sizes.add(im.size)
                    z.writestr(name, data)
                names.append(name)
                stems.append(stem)
        if zsrc is not None:
            zsrc.close()
        if len(sizes) != 1:
            raise EpisodeError("frames have different sizes: %s" % sorted(sizes))
        os.replace(tmp, osp.join(out_dir, "frames.zip"))
    finally:
        if osp.exists(tmp):
            os.remove(tmp)
        if tmp_dir:
            shutil.rmtree(tmp_dir, ignore_errors=True)
    w, h = sizes.pop()
    return names, stems, w, h


# ------------------------------------------------------------------------------------- objects, roles

def resolve_objects(ep, objects, mode):
    """Assign a CVAT role (label name) to every object.

    mode ``auto``: ``role`` or the first of ``roles``, else obj1, obj2, ...; ``id``: the object id;
    ``index``: always obj1..objK (one small label set for a project with many different episodes).
    Further entries of ``roles`` become sg-meta merges {extra role: main role}.
    Returns (list of dicts with id/role/name/level/mask candidates/boxes, alias {any id or role -> role}, merges).
    """
    taken, out = set(), []
    for k, o in enumerate(objects):
        roles = [str(r) for r in (o.get("roles") or []) if str(r).strip()]
        if mode == "auto":
            role = str(o["role"]) if o.get("role") not in (None, "") else (roles[0] if roles else None)
        elif mode == "id":
            role = str(o["id"]) if o.get("id") not in (None, "") else None
        else:
            role = None
        if role is not None:
            if role in RESERVED_LABELS:
                raise EpisodeError("object role %r is reserved; rename it or use --roles-from index" % role)
            if role in taken:
                raise EpisodeError("two objects have the role %r; give unique roles or use --roles-from index" % role)
            taken.add(role)
        out.append({"src": o, "role": role, "roles": roles, "k": k})
    n = 0
    for x in out:   # objects without a role: next free objN
        if x["role"] is None:
            n += 1
            while "obj%d" % n in taken:
                n += 1
            x["role"] = "obj%d" % n
            taken.add(x["role"])
    alias, merges, res = {}, {}, []
    for x in out:
        o, role = x["src"], x["role"]
        oid = str(o["id"]) if o.get("id") not in (None, "") else role
        for key in [oid, role] + x["roles"]:
            if key in alias and alias[key] != role:
                log_warn(ep, "%r refers to two objects; edges use the first" % key)
            alias.setdefault(key, role)
        for r in x["roles"]:
            if r != role and r not in taken and r not in RESERVED_LABELS:
                merges[r] = role
        level = o.get("level")
        res.append({"id": oid, "role": role, "name": str(o.get("name") or oid),
                    "level": int(level) if isinstance(level, (int, float)) and not isinstance(level, bool) else None,
                    "mask_dirs": [oid, role] + x["roles"], "boxes": o.get("boxes"),
                    "entity_id": str(o.get("entity_id", oid))})
    return res, alias, merges


def read_boxes(ep, obj, n):
    """Optional object boxes {frame: [x0, y0, x1, y1]} -> {int frame: box}."""
    if not isinstance(obj["boxes"], dict):
        raise EpisodeError("%s: 'boxes' must be an object {frame: [x0, y0, x1, y1]}" % obj["role"])
    out = {}
    for f, b in obj["boxes"].items():
        f = int(f)
        if not 0 <= f < n:
            log_warn(ep, "%s: box on frame %d outside 0..%d ignored" % (obj["role"], f, n - 1))
            continue
        if b is None:
            continue
        x0, y0, x1, y1 = [float(v) for v in b]
        out[f] = (min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1))
    return out


# ------------------------------------------------------------------------------------------------- XML

def fmt(v):
    return ("%.2f" % v).rstrip("0").rstrip(".") if isinstance(v, float) else str(v)


def box_xml(frame, box, outside, name):
    x0, y0, x1, y1 = box
    return ('    <box frame="%d" xtl="%s" ytl="%s" xbr="%s" ybr="%s" outside="%d" occluded="0" keyframe="1" '
            'z_order="0">\n      <attribute name="name">%s</attribute>\n    </box>\n'
            % (frame, fmt(x0), fmt(y0), fmt(x1), fmt(y1), outside, escape(name)))


def mask_xml(role, name, group, cv):
    rle, left, top, w, h = cv
    return ('    <mask label=%s source="file" occluded="0" z_order="0" group_id="%d" rle="%s" left="%d" top="%d" '
            'width="%d" height="%d">\n      <attribute name="name">%s</attribute>\n    </mask>\n'
            % (quoteattr(role), group, ",".join(map(str, rle)), left, top, w, h, escape(name)))


def tag_xml(kind, src, pred, dst):
    a, b, c = ("actor", "action", "object") if kind == "action" else ("subject", "relation", "object")
    return ('    <tag label="%s" source="file">\n      <attribute name="%s">%s</attribute>\n'
            '      <attribute name="%s">%s</attribute>\n      <attribute name="%s">%s</attribute>\n    </tag>\n'
            % (kind, a, escape(src), b, escape(pred), c, escape(dst)))


# --------------------------------------------------------------------------------------------- convert

def load_json(path, default=None):
    if not osp.exists(path):
        if default is not None:
            return default
        raise EpisodeError("%s is missing" % osp.basename(path))
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def convert_episode(ep_dir, out_root, opt):
    """Convert one episode folder; returns a dict with counts for the progress line."""
    t0 = time.time()
    ep = osp.basename(osp.normpath(ep_dir))
    graph = load_json(osp.join(ep_dir, "graph.json"))
    meta = load_json(osp.join(ep_dir, "meta.json"), {})
    if meta.get("episode") not in (None, "", ep):
        log_warn(ep, "meta.json episode %r differs from the folder name; the folder name is used" % meta["episode"])
    out = osp.join(out_root, ep)
    os.makedirs(out, exist_ok=True)
    final_xml = osp.join(out, "annotations.xml")
    if osp.exists(final_xml):
        os.remove(final_xml)          # the XML is written last: its presence marks a complete package

    objs, alias, merges = resolve_objects(ep, graph.get("objects") or [], opt.roles_from)
    spare, i = [], 0
    while len(spare) < opt.spare:      # extra1..N, skipping names an object already uses
        i += 1
        if "extra%d" % i not in alias:
            spare.append("extra%d" % i)
    roles = [o["role"] for o in objs]
    if not roles and not spare:
        raise EpisodeError("graph.json has no objects")

    names, stems, W, H = write_frames_zip(ep_dir, out, opt.video, opt.video_format)
    n = len(names)
    if isinstance(meta.get("n_frames"), int) and meta["n_frames"] != n:
        log_warn(ep, "meta.json n_frames=%d but %d frames found" % (meta["n_frames"], n))

    # edges -> {frame: [(kind, src, pred, dst)]}
    edges, n_skipped = {}, 0
    raw_edges = graph.get("edges") or []
    vocab = {"relation": [], "action": []}
    seen = set()
    for e in raw_edges:
        kind = "action" if str(e.get("type", "relation")).strip().lower() == "action" else "relation"
        pred = str(e.get("predicate") or "").strip()
        src = alias.get(str(e.get("subject")))
        dst = NONE if is_none(e.get("object")) else alias.get(str(e.get("object")))
        if not pred or src is None or dst is None:
            n_skipped += 1
            log_warn(ep, "edge skipped (empty predicate or unknown object): %s"
                     % json.dumps({k: e.get(k) for k in ("type", "subject", "predicate", "object")}))
            continue
        if dst == src:
            dst = NONE
        if pred not in vocab[kind]:
            vocab[kind].append(pred)
        bad = 0
        for f in sorted(edge_frames(e)):
            if not 0 <= f < n:
                bad += 1
                continue
            key = (f, kind, src, pred, dst)
            if key not in seen:
                seen.add(key)
                edges.setdefault(f, []).append(key[1:])
        if bad:
            log_warn(ep, "%s %s %s %s: %d frame(s) outside 0..%d dropped" % (kind, src, pred, dst, bad, n - 1))

    # ontology: --ontology file, else graph.json / meta.json "ontology"; predicates found in the edges are added
    base = opt.ontology_data or graph.get("ontology") or meta.get("ontology") or {}
    states = [str(s) for s in base.get("states", [])]
    actions = [str(s) for s in base.get("actions", [])]
    states += [p for p in vocab["relation"] if p not in states]
    actions += [p for p in vocab["action"] if p not in actions]

    # masks and boxes per object
    mask_root = osp.join(ep_dir, "masks")
    tracks_xml, masks_on = [], {}    # masks_on[frame] = [mask xml]
    n_masks = n_boxes = 0
    for k, o in enumerate(objs):
        group = k + 1
        mdir = next((osp.join(mask_root, d) for d in o["mask_dirs"] if osp.isdir(osp.join(mask_root, d))), None)
        from_masks = {}
        if mdir:
            files = set(os.listdir(mdir))
            for i in range(n):
                fn = "frame_%06d.png" % i
                if fn not in files:
                    fn = stems[i] + ".png"
                    if fn not in files:
                        continue
                m = load_mask(osp.join(mdir, fn))
                if m.shape != (H, W):
                    log_warn(ep, "%s: mask %s is %dx%d, frames are %dx%d; skipped"
                             % (o["role"], fn, m.shape[1], m.shape[0], W, H))
                    continue
                cv = mask_to_cvat(m)
                if cv is None:
                    continue
                masks_on.setdefault(i, []).append(mask_xml(o["role"], o["name"], group, cv))
                from_masks[i] = tuple(mask_bbox(m))
                n_masks += 1
            extra = sorted(f for f in files if f.startswith("frame_") and f.endswith(".png")
                           and f[6:-4].isdigit() and int(f[6:-4]) >= n)
            if extra:
                log_warn(ep, "%s: %d mask file(s) beyond the last frame ignored" % (o["role"], len(extra)))
        boxes = read_boxes(ep, o, n) if o["boxes"] is not None else from_masks
        if not boxes:   # CVAT rejects a track without shapes
            continue
        x = ['  <track id="%d" label=%s source="file" group_id="%d">\n' % (k, quoteattr(o["role"]), group)]
        for i in sorted(boxes):
            x.append(box_xml(i, boxes[i], 0, o["name"]))
            if i + 1 < n and i + 1 not in boxes:     # object leaves the view: close the track
                x.append(box_xml(i + 1, boxes[i], 1, o["name"]))
            n_boxes += 1
        x.append("  </track>\n")
        tracks_xml.extend(x)

    # labels and guide
    with open(osp.join(out, "labels.json"), "w", encoding="utf-8") as f:
        json.dump(labels_json(roles, spare), f, indent=1, ensure_ascii=False)
    entities = []
    for o in objs:
        e = {"entity_id": o["entity_id"], "role": o["role"], "name": o["name"]}
        if o["level"] is not None:
            e["level"] = o["level"]
        entities.append(e)
    extra_meta = meta.get("sg_meta_extra") if isinstance(meta.get("sg_meta_extra"), dict) else None
    with open(osp.join(out, "guide.md"), "w", encoding="utf-8") as f:
        f.write(guide_md(ep, str(meta.get("goal") or ""), entities, {"states": states, "actions": actions},
                         merges, extra_meta, spare, opt.checklist_text))

    # annotations.xml (written last, atomically)
    x = ['<?xml version="1.0" encoding="utf-8"?>\n<annotations>\n  <version>1.1</version>\n']
    x.extend(tracks_xml)
    n_tags = 0
    for i in range(n):
        x.append('  <image id="%d" name=%s width="%d" height="%d">\n' % (i, quoteattr(names[i]), W, H))
        x.extend(masks_on.get(i, []))
        for kind, src, pred, dst in edges.get(i, []):
            x.append(tag_xml(kind, src, pred, dst))
            n_tags += 1
        x.append("  </image>\n")
    x.append("</annotations>\n")
    tmp = final_xml + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write("".join(x))
    os.replace(tmp, final_xml)
    return {"episode": ep, "frames": n, "size": "%dx%d" % (W, H), "objects": len(objs), "masks": n_masks,
            "boxes": n_boxes, "edges": len(raw_edges) - n_skipped, "tags": n_tags, "seconds": time.time() - t0}


def _worker(args):
    ep_dir, out_root, opt = args
    try:
        return convert_episode(ep_dir, out_root, opt)
    except Exception as e:   # one broken episode must not stop a batch
        return {"episode": osp.basename(osp.normpath(ep_dir)), "error": "%s: %s" % (type(e).__name__, e)}


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter,
                                 epilog="Formats: docs/DATA_FORMAT.md")
    ap.add_argument("input", help="episode folder, or a folder of episode folders with --all")
    ap.add_argument("out", help="output folder; each package goes to OUT/<episode>/")
    ap.add_argument("--all", action="store_true", help="convert every sub-folder of INPUT that has a graph.json")
    ap.add_argument("--ontology", help="JSON {\"states\": [...], \"actions\": [...]}: suggestion lists for the "
                                       "panel (predicates found in the edges are added)")
    ap.add_argument("--spare", type=int, default=2, help="number of empty extra roles extra1..N (default 2)")
    ap.add_argument("--roles-from", choices=("auto", "id", "index"), default="auto",
                    help="auto: object 'role'/'roles' else objN; id: object id; index: always obj1..objK")
    ap.add_argument("--video", help="take frames from this video (single episode only; needs ffmpeg)")
    ap.add_argument("--video-format", choices=("jpg", "png"), default="jpg",
                    help="image format of frames extracted from a video (default jpg)")
    ap.add_argument("--checklist", help="markdown file that replaces the default checklist in guide.md")
    ap.add_argument("--skip-existing", action="store_true", help="skip episodes whose package is complete")
    ap.add_argument("--workers", type=int, default=1, help="parallel processes for --all (default 1)")
    a = ap.parse_args()

    if not osp.isdir(a.input):
        ap.error("%s is not a folder" % a.input)
    if a.all:
        eps = sorted((osp.join(a.input, d) for d in os.listdir(a.input)
                      if osp.isfile(osp.join(a.input, d, "graph.json"))), key=natural_key)
        if a.video:
            ap.error("--video works with a single episode only (or put video.mp4 into each episode folder)")
    else:
        if not osp.isfile(osp.join(a.input, "graph.json")):
            hint = " (use --all for a folder of episodes)" if any(
                osp.isfile(osp.join(a.input, d, "graph.json")) for d in os.listdir(a.input)) else ""
            ap.error("%s has no graph.json%s" % (a.input, hint))
        eps = [a.input]
    if a.spare < 0:
        ap.error("--spare must be >= 0")
    a.ontology_data = None
    if a.ontology:
        with open(a.ontology, encoding="utf-8") as f:
            a.ontology_data = json.load(f)
    a.checklist_text = None
    if a.checklist:
        with open(a.checklist, encoding="utf-8") as f:
            a.checklist_text = f.read()
    os.makedirs(a.out, exist_ok=True)
    if a.skip_existing:
        todo = [d for d in eps if not osp.exists(osp.join(a.out, osp.basename(osp.normpath(d)), "annotations.xml"))]
        if len(todo) < len(eps):
            print("skipping %d episode(s) with a complete package" % (len(eps) - len(todo)))
        eps = todo
    print("converting %d episode(s) -> %s" % (len(eps), a.out), flush=True)

    jobs = [(d, a.out, a) for d in eps]
    failed = 0

    def report(j, r):
        if "error" in r:
            print("[%d/%d] %s: FAILED %s" % (j, len(eps), r["episode"], r["error"]), flush=True)
            return 1
        print("[%d/%d] %s: %d frames %s, %d objects, %d masks, %d boxes, %d edges as %d frame tags, %.1f s"
              % (j, len(eps), r["episode"], r["frames"], r["size"], r["objects"], r["masks"], r["boxes"],
                 r["edges"], r["tags"], r["seconds"]), flush=True)
        return 0

    if a.workers <= 1:
        for j, job in enumerate(jobs, 1):
            failed += report(j, _worker(job))
    else:
        with ProcessPoolExecutor(a.workers) as ex:
            for j, r in enumerate(ex.map(_worker, jobs), 1):
                failed += report(j, r)
    print("done: %d converted, %d failed" % (len(eps) - failed, failed))
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
