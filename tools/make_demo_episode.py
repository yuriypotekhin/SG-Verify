#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Generate a synthetic, license-free demo episode in the SG-Verify input format.

A gripper reaches for a cup standing on a table, grabs it, lifts it, carries it to a box that slides in
mid-video and puts it into the box. Everything is drawn from simple shapes, so the result can be shared
freely and is fully deterministic for a given --seed.

Output::

    OUT/NAME/frames/frame_%06d.png          RGB frames
    OUT/NAME/masks/<object>/frame_%06d.png  visible part of each object, 0/255, one file per frame
    OUT/NAME/graph.json                     objects (gripper, cup, table, box) and edges over frames
    OUT/NAME/meta.json                      goal sentence and generation parameters

Edges: actions gripper reach_for / grab / lift / move / place / release cup; relations cup on table,
gripper holding cup, cup inside box, box on table and the unary state box open.

Usage::

    make_demo_episode.py --out demo_data [--name demo_episode] [--frames 30] [--size 640x480] [--seed 0]
"""
import argparse
import json
import os
import os.path as osp

import numpy as np
from PIL import Image, ImageDraw

OBJECTS = [   # z-order of drawing: table, cup, box, gripper (later objects hide earlier ones)
    {"id": "gripper", "roles": ["gripper"], "name": "robot gripper", "level": 0},
    {"id": "cup", "roles": ["cup"], "name": "red cup", "level": 1},
    {"id": "table", "roles": ["table"], "name": "wooden table", "level": 2},
    {"id": "box", "roles": ["box"], "name": "cardboard box", "level": 2},
]
GOAL = "Pick up the red cup from the table and put it into the cardboard box."

# action phases as fractions of the episode: (start, end, action)
PHASES = [(0.00, 0.24, "reach_for"), (0.24, 0.32, "grab"), (0.32, 0.44, "lift"),
          (0.44, 0.66, "move"), (0.66, 0.78, "place"), (0.78, 0.86, "release")]
BOX_IN = (0.30, 0.42)   # the box slides in from the right edge during this part of the episode


def smooth(a, b, t, t0, t1):
    """Smooth-step interpolation from a to b while t goes from t0 to t1 (clamped)."""
    u = min(max((t - t0) / (t1 - t0), 0.0), 1.0) if t1 > t0 else 1.0
    u = u * u * (3 - 2 * u)
    return a + (b - a) * u


class Scene:
    def __init__(self, w, h, seed):
        rng = np.random.RandomState(seed)
        self.w, self.h = w, h
        s = min(w, h)
        self.r = max(3.0, 0.06 * s)                                   # cup radius
        self.table = (0.06 * w, 0.68 * h, 0.94 * w, 0.74 * h)        # table top
        self.legs = [(0.10 * w, 0.74 * h, 0.13 * w, 0.98 * h), (0.87 * w, 0.74 * h, 0.90 * w, 0.98 * h)]
        ty = self.table[1]
        self.cup0 = ((0.28 + rng.uniform(-0.03, 0.03)) * w, ty - self.r)
        self.bw, self.bh = max(4.2 * self.r, 0.2 * w), 1.4 * self.r  # box size (open top)
        self.box_x = (0.72 + rng.uniform(-0.03, 0.03)) * w            # final box centre
        self.cup_color = tuple(int(c) for c in (200 + rng.randint(0, 40), 30 + rng.randint(0, 30), 40))
        self.fl = 1.8 * self.r                                         # finger length
        self.fw = max(2.0, 0.3 * self.r)                               # finger width
        self.gap_open, self.gap_closed = 2.8 * self.r, 1.9 * self.r
        grasp_py = self.cup0[1] + 0.5 * self.r - self.fl              # palm y when the fingers hold the cup
        lift_cy = ty - self.bh - 1.6 * self.r                          # cup centre while carried
        self.in_cy = ty - self.r - 1                                   # cup centre inside the box
        off = self.fl - 0.5 * self.r                                   # cup centre below the palm
        top_py = max(0.18 * h, 3 * self.fw)
        self.keys = {"start": (0.55 * w, top_py), "above": (self.cup0[0], min(top_py, grasp_py)),
                     "grasp": (self.cup0[0], grasp_py), "lift": (self.cup0[0], lift_cy - off),
                     "over": (self.box_x, lift_cy - off), "in": (self.box_x, self.in_cy - off),
                     "up": (self.box_x, top_py)}
        self.off = off
        self.bg = self._background()

    def _background(self):
        w, h = self.w, self.h
        y = np.linspace(0, 1, h)[:, None, None]
        wall = np.array([214, 222, 232]) * (1 - 0.25 * y) + np.array([150, 160, 175]) * 0.25 * y
        img = np.broadcast_to(wall, (h, w, 3)).copy()
        img[int(0.86 * h):] = [120, 110, 100]                           # floor
        return img.astype(np.uint8)

    def state(self, t):
        """Gripper palm position, finger gap, cup centre and box centre at time t in [0, 1]."""
        k = self.keys
        path = [(0.00, 0.12, "start", "above"), (0.12, 0.24, "above", "grasp"), (0.32, 0.44, "grasp", "lift"),
                (0.44, 0.66, "lift", "over"), (0.66, 0.78, "over", "in"), (0.86, 1.00, "in", "up")]
        px, py = k["start"]
        for t0, t1, a, b in path:
            if t >= t0:
                px = smooth(k[a][0], k[b][0], t, t0, t1)
                py = smooth(k[a][1], k[b][1], t, t0, t1)
        gap = self.gap_open
        if t >= 0.24:
            gap = smooth(self.gap_open, self.gap_closed, t, 0.24, 0.32)
        if t >= 0.78:
            gap = smooth(self.gap_closed, self.gap_open, t, 0.78, 0.86)
        if t < 0.32:
            cup = self.cup0
        elif t < 0.78:
            cup = (px, py + self.off)
        else:
            cup = (self.box_x, self.in_cy)
        bx = smooth(self.w + self.bw / 2 + 1, self.box_x, t, *BOX_IN) if t >= BOX_IN[0] else None
        return (px, py), gap, cup, bx

    def render(self, t):
        """RGB frame (H, W, 3) and visible masks {object id: bool array} at time t."""
        (px, py), gap, (cx, cy), bx = self.state(t)
        img = Image.fromarray(self.bg)
        ids = Image.new("L", (self.w, self.h), 0)
        d, di = ImageDraw.Draw(img), ImageDraw.Draw(ids)
        order = {o["id"]: i + 1 for i, o in enumerate(OBJECTS)}

        def rect(box, color, oid, outline=None):
            d.rectangle(box, fill=color, outline=outline)
            di.rectangle(box, fill=order[oid])

        # table
        rect(self.table, (150, 100, 60), "table", (110, 70, 40))
        for leg in self.legs:
            rect(leg, (130, 85, 50), "table")
        # cup: a disc with a highlight
        r = self.r
        d.ellipse((cx - r, cy - r, cx + r, cy + r), fill=self.cup_color, outline=(120, 20, 20))
        di.ellipse((cx - r, cy - r, cx + r, cy + r), fill=order["cup"])
        d.ellipse((cx - 0.55 * r, cy - 0.6 * r, cx - 0.1 * r, cy - 0.2 * r), fill=(250, 170, 170))
        # box: open-top box with a lid flap, drawn in front of the cup
        if bx is not None:
            ty = self.table[1]
            x0, x1 = bx - self.bw / 2, bx + self.bw / 2
            rect((x0, ty - self.bh, x1, ty), (196, 150, 95), "box", (140, 100, 60))
            lid = [(x1, ty - self.bh), (x1 + 0.35 * self.bh, ty - 1.8 * self.bh),
                   (x1 + 0.45 * self.bh, ty - 1.7 * self.bh), (x1 + 0.1 * self.bh, ty - self.bh)]
            d.polygon(lid, fill=(176, 130, 80))
            di.polygon(lid, fill=order["box"])
        # gripper: arm from the top edge, palm and two fingers
        aw, ph, fw = max(2.0, 0.5 * r), max(2.0, 0.35 * r), self.fw
        grey, dark = (90, 95, 105), (60, 62, 70)
        rect((px - aw / 2, 0, px + aw / 2, py - ph), grey, "gripper")
        rect((px - gap / 2 - fw, py - ph, px + gap / 2 + fw, py), dark, "gripper")
        rect((px - gap / 2 - fw, py, px - gap / 2, py + self.fl), dark, "gripper")
        rect((px + gap / 2, py, px + gap / 2 + fw, py + self.fl), dark, "gripper")

        lab = np.asarray(ids)
        return np.asarray(img), {o["id"]: lab == order[o["id"]] for o in OBJECTS}

    def relations(self, t, masks):
        """Relations true at time t: [(subject, predicate, object or None)]."""
        (px, py), gap, (cx, cy), bx = self.state(t)
        out = []
        if t < 0.32:
            out.append(("cup", "on", "table"))
        if 0.30 <= t < 0.80:
            out.append(("gripper", "holding", "cup"))
        if masks["box"].any():
            out.append(("box", "on", "table"))
            out.append(("box", "open", None))
            ty = self.table[1]
            if bx is not None and abs(cx - bx) < self.bw / 2 and cy + self.r > ty - self.bh + 0.5 * self.r:
                out.append(("cup", "inside", "box"))
        return out


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", required=True, help="output root; the episode goes to OUT/NAME/")
    ap.add_argument("--name", default="demo_episode", help="episode name (folder and CVAT task name)")
    ap.add_argument("--frames", type=int, default=30, help="number of frames (default 30)")
    ap.add_argument("--size", default="640x480", help="frame size WxH (default 640x480)")
    ap.add_argument("--seed", type=int, default=0, help="seed for the small layout variations (default 0)")
    a = ap.parse_args()
    try:
        w, h = [int(v) for v in a.size.lower().split("x")]
    except ValueError:
        ap.error("--size must look like 640x480")
    if w < 64 or h < 48:
        ap.error("--size must be at least 64x48")
    if a.frames < 1:
        ap.error("--frames must be >= 1")

    ep = osp.join(a.out, a.name)
    os.makedirs(osp.join(ep, "frames"), exist_ok=True)
    for o in OBJECTS:
        os.makedirs(osp.join(ep, "masks", o["id"]), exist_ok=True)
    scene = Scene(w, h, a.seed)
    edges = {}   # (type, subject, predicate, object) -> frames
    n = a.frames
    for i in range(n):
        t = i / (n - 1) if n > 1 else 0.0
        img, masks = scene.render(t)
        Image.fromarray(img).save(osp.join(ep, "frames", "frame_%06d.png" % i))
        for oid, m in masks.items():
            Image.fromarray(m.astype(np.uint8) * 255).save(osp.join(ep, "masks", oid, "frame_%06d.png" % i))
        for t0, t1, act in PHASES:
            if t0 <= t < t1:
                edges.setdefault(("action", "gripper", act, "cup"), []).append(i)
        for s, p, o in scene.relations(t, masks):
            edges.setdefault(("relation", s, p, o), []).append(i)

    graph = {"objects": OBJECTS,
             "edges": [{"type": k[0], "subject": k[1], "predicate": k[2], "object": k[3], "frames": v}
                       for k, v in sorted(edges.items(), key=lambda kv: (kv[1][0], kv[0][0], kv[0][2]))]}
    with open(osp.join(ep, "graph.json"), "w", encoding="utf-8") as f:
        json.dump(graph, f, indent=1)
    with open(osp.join(ep, "meta.json"), "w", encoding="utf-8") as f:
        json.dump({"episode": a.name, "goal": GOAL, "source": "synthetic demo (make_demo_episode.py)",
                   "seed": a.seed, "n_frames": n, "frame_size": [w, h]}, f, indent=1)
    print("%s: %d frames %dx%d, %d objects, %d edges" % (ep, n, w, h, len(OBJECTS), len(graph["edges"])))


if __name__ == "__main__":
    main()
