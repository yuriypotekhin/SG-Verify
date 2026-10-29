#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Shared helpers of the SG-Verify data tools (episode folder <-> CVAT package <-> CVAT export).

This module is not a command-line tool. It is imported by ``to_cvat.py``, ``upload_episodes.py`` and
``export_episodes.py`` and holds everything the three of them must agree on:

* CVAT mask RLE (encode / decode) -- the same layout as ``cvat-core/src/rle-utils.ts``: run lengths over the
  mask's bounding box in row-major order, the first run counts zeros; a mask shape stores the runs followed
  by ``[left, top, right, bottom]`` (inclusive pixel coordinates);
* the label set of a task (one label per object role + the ``relation`` and ``action`` tag labels);
* the task guide with the machine-readable ``<!-- sg-meta {...} -->`` block read by the scene-graph panel;
* small utilities: natural sorting, mask loading, frame lists <-> intervals.

See ``docs/DATA_FORMAT.md`` for the formats themselves.
"""
import json
import re

import numpy as np
from PIL import Image

IMAGE_EXTS = (".png", ".jpg", ".jpeg")
VIDEO_EXTS = (".mp4", ".avi", ".mov", ".mkv", ".webm")

# label names with a fixed meaning; an object role may not use them
RESERVED_LABELS = ("relation", "action", "none")
TAG_LABELS = ("relation", "action")
# tag label -> names of its (source, predicate, target) attributes
TAG_FIELDS = {"relation": ("subject", "relation", "object"), "action": ("actor", "action", "object")}
NONE = "none"

# 20 distinct colours (Sasha Trubetskoy's list), assigned to role labels by position
PALETTE = ["#e6194b", "#3cb44b", "#4363d8", "#f58231", "#911eb4", "#42d4f4", "#f032e6", "#bfef45",
           "#fabed4", "#469990", "#dcbeff", "#9a6324", "#fffac8", "#800000", "#aaffc3", "#808000",
           "#ffd8b1", "#000075", "#a9a9a9", "#ffe119"]

SG_META_RE = re.compile(r"<!--\s*sg-meta\s+(\{[\s\S]*?\})\s*-->")

DEFAULT_CHECKLIST = """\
## How to check this episode

1. **Goal.** Read the goal above the graph and play the video. If the video does not show what the goal
   says, press **Goal mismatch** -- the episode is saved and needs no further checking.
2. **Objects.** Compare the object names in the graph with the video. Wrong name: double-click the node
   and rename it on all frames.{spare_hint}
3. **Boxes and masks.** Go to the frame where a box or mask goes wrong, fix it there and propagate the fix
   with the panel tools (track the mask, interpolate or copy the box) over a frame range.
4. **Relations and actions.** For every edge check its type, its objects and its first and last frame on
   the timeline. Add, rename or delete edges on one frame, a run of frames, a range or all frames.
5. Everything is right: press **Verified**. `Ctrl+Z` undoes any edit, `Ctrl+S` saves without a verdict.
"""

SPARE_HINT = ("\n   A missed object: draw its box or mask with a spare label ({labels})\n"
              "   and set its `name` attribute.")


def natural_key(s):
    """Sort key that orders 'frame2' before 'frame10'."""
    return [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", str(s))]


def load_mask(path_or_file):
    """Read a mask image as a 2-D bool array; any non-zero pixel is foreground."""
    a = np.asarray(Image.open(path_or_file))
    if a.ndim == 3:
        rgb = a[..., :3].max(axis=2) > 0
        if not rgb.any() and a.shape[2] == 4:   # black RGBA mask drawn with alpha only
            return a[..., 3] > 0
        return rgb
    return a > 0


# ---------------------------------------------------------------------------------------------- mask RLE

def rle_encode(mask):
    """CVAT RLE of a 2-D bool array: run lengths in row-major order, the first run counts zeros."""
    flat = np.asarray(mask, dtype=np.uint8).ravel()
    if flat.size == 0:
        return []
    change = np.flatnonzero(np.diff(flat)) + 1
    runs = np.diff(np.concatenate(([0], change, [flat.size]))).tolist()
    return ([0] if flat[0] else []) + runs


def mask_to_cvat(mask):
    """Full-frame bool mask -> (rle, left, top, width, height) of its bounding box, or None if empty."""
    ys, xs = np.nonzero(mask)
    if xs.size == 0:
        return None
    left, top, right, bottom = int(xs.min()), int(ys.min()), int(xs.max()), int(ys.max())
    return rle_encode(mask[top:bottom + 1, left:right + 1]), left, top, right - left + 1, bottom - top + 1


def rle_decode(points, width, height):
    """Points of a CVAT mask shape (RLE runs + [left, top, right, bottom]) -> uint8 0/1 array (height, width).

    Parts of the mask outside the frame are clipped.
    """
    p = [int(round(v)) for v in points]
    if len(p) < 4:
        return np.zeros((height, width), np.uint8)
    left, top, right, bottom = p[-4:]
    w, h = right - left + 1, bottom - top + 1
    full = np.zeros((height, width), np.uint8)
    if w <= 0 or h <= 0:
        return full
    runs = np.maximum(np.asarray(p[:-4], dtype=np.int64), 0)
    flat = np.repeat((np.arange(runs.size) % 2).astype(np.uint8), runs)[:w * h]
    if flat.size < w * h:
        flat = np.concatenate([flat, np.zeros(w * h - flat.size, np.uint8)])
    crop = flat.reshape(h, w)
    x0, y0, x1, y1 = max(left, 0), max(top, 0), min(left + w, width), min(top + h, height)
    if x1 > x0 and y1 > y0:
        full[y0:y1, x0:x1] = crop[y0 - top:y1 - top, x0 - left:x1 - left]
    return full


def mask_bbox(mask):
    """Bounding box [x0, y0, x1, y1] of a bool mask in pixel-edge coordinates (x1, y1 exclusive), or None."""
    ys, xs = np.nonzero(mask)
    if xs.size == 0:
        return None
    return [int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1]


# ------------------------------------------------------------------------------------------ frame lists

def frames_to_intervals(frames):
    """[0, 1, 2, 5, 6] -> [[0, 2], [5, 6]] (inclusive)."""
    out = []
    for f in sorted(set(int(x) for x in frames)):
        if out and f == out[-1][1] + 1:
            out[-1][1] = f
        else:
            out.append([f, f])
    return out


def edge_frames(edge):
    """Set of frames of a graph.json edge: union of its ``frames`` list and ``intervals`` ([start, end], inclusive).

    A ``frames`` item may itself be a [start, end] pair.
    """
    out = set()
    items = list(edge.get("frames") or [])
    pairs = list(edge.get("intervals") or [])
    for x in items:
        if isinstance(x, (list, tuple)):
            pairs.append(x)
        else:
            out.add(int(x))
    for pr in pairs:
        if len(pr) != 2:
            raise ValueError("interval must be [start, end], got %r" % (pr,))
        a, b = int(pr[0]), int(pr[1])
        out.update(range(min(a, b), max(a, b) + 1))
    return out


def is_none(value):
    return value is None or str(value).strip() in ("", "none", "None", "null")


# ------------------------------------------------------------------------------------- labels and guide

def _text_attr(name):
    return {"name": name, "input_type": "text", "mutable": False, "default_value": "", "values": [""]}


def _select_attr(name, values, default):
    return {"name": name, "input_type": "select", "mutable": False, "default_value": default, "values": list(values)}


def labels_json(roles, spare=()):
    """Label spec for POST /api/tasks or /api/projects.

    One label of type ``any`` per role (object roles first, then spare roles) with a free-text ``name``
    attribute; tag labels ``relation`` (subject / relation / object) and ``action`` (actor / action / object).
    The predicate is free text (the vocabulary is only a suggestion list in the guide); ``object`` is
    ``none`` for a unary edge (a state of one object).
    """
    all_roles = list(roles) + list(spare)
    if not all_roles:
        raise ValueError("a task needs at least one object role")
    out = [{"name": r, "color": PALETTE[i % len(PALETTE)], "type": "any", "attributes": [_text_attr("name")]}
           for i, r in enumerate(all_roles)]
    with_none = [NONE] + all_roles
    out.append({"name": "relation", "color": "#000000", "type": "tag", "attributes": [
        _select_attr("subject", all_roles, all_roles[0]), _text_attr("relation"),
        _select_attr("object", with_none, NONE)]})
    out.append({"name": "action", "color": "#808080", "type": "tag", "attributes": [
        _select_attr("actor", all_roles, all_roles[0]), _text_attr("action"),
        _select_attr("object", with_none, NONE)]})
    return out


def sg_meta_json(meta):
    """Serialise the sg-meta dict so that it can never close the HTML comment early."""
    return json.dumps(meta, ensure_ascii=False).replace("-->", "--\\u003e")


def parse_sg_meta(markdown):
    """The sg-meta dict of a task guide, or {} if the guide has none (or it is not valid JSON)."""
    m = SG_META_RE.search(markdown or "")
    if not m:
        return {}
    try:
        meta = json.loads(m.group(1))
    except ValueError:
        return {}
    return meta if isinstance(meta, dict) else {}


def _cell(s):
    return str(s).replace("|", "\\|").replace("\n", " ")


def guide_md(episode, goal, entities, ontology, merges=None, extra=None, spare=(), checklist=None):
    """Task guide: episode, goal, role -> object table, checklist and the sg-meta block.

    ``entities``: [{"entity_id", "role", "name", "level"?}]; ``ontology``: {"states": [...], "actions": [...]};
    ``merges``: {role: role it belongs to}; ``extra``: further sg-meta keys passed through unchanged.
    """
    meta = {}
    for k, v in (extra or {}).items():
        meta[k] = v
    meta.update({"episode": episode, "goal": goal, "entities": entities, "ontology": ontology})
    if merges:
        meta["merges"] = merges
    also = {}
    for src, dst in (merges or {}).items():
        also.setdefault(dst, []).append(src)
    rows = "\n".join("| `%s`%s | %s |" % (_cell(e["role"]),
                                          "".join(" `%s`" % _cell(r) for r in also.get(e["role"], [])),
                                          _cell(e.get("name", ""))) for e in entities)
    if checklist is None:
        hint = SPARE_HINT.format(labels=", ".join("`%s`" % s for s in spare)) if spare else ""
        checklist = DEFAULT_CHECKLIST.format(spare_hint=hint)
    return ("**Episode:** `%s`\n\n**Goal:** %s\n\n| role | object |\n|---|---|\n%s\n\n%s\n<!-- sg-meta %s -->\n"
            % (_cell(episode), goal or "-", rows, checklist.rstrip() + "\n", sg_meta_json(meta)))
