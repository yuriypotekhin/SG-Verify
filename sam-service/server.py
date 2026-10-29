# -*- coding: utf-8 -*-
"""SG-Verify tracking service: SAM3 video tracker behind a small HTTP API used by the scene-graph panel.
The API is the same as the earlier SAM2 service (the panel calls /sg-tools/sam2/...; /sg-tools/sam3/... is an alias).

POST /sg-tools/sam2/track
  {"episode": "<task name = episode name>", "frame": 3, "to_frame": 29,
   "mask": {"rle": [..], "left": l, "top": t, "width": w, "height": h}}
-> {"frames": [{"frame": 4, "rle": [...], "left":..,"top":..,"width":..,"height":.., "bbox": [x0,y0,x1,y1]}, ...]}
POST /sg-tools/sam2/from_box
  {"episode": ..., "boxes": [{"frame": 5, "box": [x0, y0, x1, y1]}, ...]} -> masks of these frames
POST /sg-tools/sam3/refine  -- mask refinement by clicks on one frame (see refine())

Model: the SAM3 tracker with the detector backbone, as in the official example
examples/sam3_for_sam2_video_task_example.ipynb: predictor = model.tracker; predictor.backbone = model.detector.backbone.
RLE: as in CVAT (cvat-core/src/rle-utils.ts): row by row inside the mask bounding box, the first run is zeros.
Frames are read from $PACKAGES/<episode>/frames.zip (the same zip that was uploaded to CVAT);
tools/upload_episodes.py --packages-dir puts them there.

Environment: SAM3_CKPT (weights, default /models/sam3.pt), PACKAGES (one or more roots separated by ':'),
SAM3_BF16 (1), SAM3_MAX_STATES (episodes kept on the GPU, 2), SG_GPU_MEMORY_GB (cap of GPU memory for this process).
"""
import io, os, tempfile, threading, zipfile

import numpy as np
import torch
from flask import Flask, jsonify, request
from PIL import Image

CKPT = os.environ.get("SAM3_CKPT", "/models/sam3.pt")
PACKAGES = os.environ.get("PACKAGES", "/packages")
USE_BF16 = os.environ.get("SAM3_BF16", "1") == "1"   # bf16 is the native SAM3 mode on Ampere (RTX 30xx) and newer
MAX_STATES = int(os.environ.get("SAM3_MAX_STATES", "2"))
GPU_MEMORY_GB = float(os.environ.get("SG_GPU_MEMORY_GB", "") or 0)   # 0 = no cap
app = Flask(__name__)
lock = threading.Lock()
predictor = None


def rle_decode(rle, w, h):
    flat = np.zeros(w * h, np.uint8); pos = 0; val = 0
    for c in rle:
        flat[pos:pos + c] = val; pos += c; val ^= 1
    return flat.reshape(h, w).astype(bool)


def rle_encode(m):
    flat = m.ravel().astype(np.uint8)
    change = np.flatnonzero(np.diff(flat)) + 1
    runs = np.diff(np.concatenate(([0], change, [flat.size]))).tolist()
    return ([0] if flat[0] else []) + runs


def mask_to_cvat(m):
    ys, xs = np.nonzero(m)
    if len(xs) == 0:
        return None
    l, t, r, b = int(xs.min()), int(ys.min()), int(xs.max()), int(ys.max())
    return {"rle": rle_encode(m[t:b + 1, l:r + 1]), "left": l, "top": t, "width": r - l + 1, "height": b - t + 1,
            "bbox": [l, t, r, b]}


def to_hw(masks):
    """Mask of one object from the SAM3 output -> boolean H x W array."""
    m = masks[0]
    while m.dim() > 2:
        m = m[0]
    return (m > 0).cpu().numpy()


_frames_cache = {}


def frames_dir(episode):
    """Episode frames as 000000.jpg ... in a directory: the SAM3 (and SAM2) loader reads jpg only."""
    if episode in _frames_cache and os.path.isdir(_frames_cache[episode]):
        return _frames_cache[episode]
    for root in PACKAGES.split(":"):
        zpath = os.path.join(root, episode, "frames.zip")
        if os.path.exists(zpath):
            break
    else:
        raise FileNotFoundError(f"frames.zip for {episode} not found in {PACKAGES}")
    d = tempfile.mkdtemp(prefix="sg_")
    with zipfile.ZipFile(zpath) as z:
        names = sorted(n for n in z.namelist() if n.lower().endswith((".png", ".jpg", ".jpeg")))   # HIG — jpg
        for i, n in enumerate(names):
            Image.open(io.BytesIO(z.read(n))).convert("RGB").save(os.path.join(d, f"{i:06d}.jpg"), quality=95)
    _frames_cache[episode] = d
    return d


def get_predictor():
    global predictor
    if predictor is None:
        if GPU_MEMORY_GB > 0:   # share the GPU with other processes: cap this process
            total = torch.cuda.get_device_properties(0).total_memory / 2 ** 30
            torch.cuda.set_per_process_memory_fraction(min(1.0, GPU_MEMORY_GB / total), 0)
        from sam3.model_builder import build_sam3_video_model
        model = build_sam3_video_model(checkpoint_path=CKPT, load_from_HF=False, device="cuda")
        predictor = model.tracker
        predictor.backbone = model.detector.backbone
    return predictor


def autocast():
    return torch.autocast("cuda", dtype=torch.bfloat16, enabled=USE_BF16)


# the state (frames + their features) is kept per episode: a second object of the same episode is tracked without
# recomputing frame features. At most MAX_STATES episodes are kept on the GPU.
_states = {}


def get_state(p, ep, d):
    if ep in _states:
        _states[ep] = _states.pop(ep)
        return _states[ep]
    while len(_states) >= MAX_STATES:
        _states.pop(next(iter(_states)))
        torch.cuda.empty_cache()
    _states[ep] = p.init_state(video_path=d, offload_video_to_cpu=False, async_loading_frames=False)
    return _states[ep]


@app.get("/sg-tools/health")
def health():
    return jsonify({"ok": True, "model": "sam3", "refine": True, "cuda": torch.cuda.is_available(),
                    "loaded": predictor is not None})


@app.post("/sg-tools/sam2/track")
@app.post("/sg-tools/sam3/track", endpoint="track3")
def track():
    q = request.get_json(force=True)
    ep, f0, f1, m = q["episode"], int(q["frame"]), int(q["to_frame"]), q["mask"]
    d = frames_dir(ep)
    n = len(os.listdir(d))
    W, H = Image.open(os.path.join(d, "000000.jpg")).size
    full = np.zeros((H, W), bool)
    sub = rle_decode(m["rle"], m["width"], m["height"])
    full[m["top"]:m["top"] + m["height"], m["left"]:m["left"] + m["width"]] = sub
    out = []
    with lock, torch.inference_mode(), autocast():
        p = get_predictor()
        state = get_state(p, ep, d)
        p.clear_all_points_in_video(state)   # drops previous objects and results, frame features stay
        p.add_new_mask(state, frame_idx=f0, obj_id=1, mask=torch.from_numpy(full))
        for fi, _ids, _low, video_masks, _scores in p.propagate_in_video(
                state, start_frame_idx=f0, max_frame_num_to_track=abs(f1 - f0), reverse=f1 < f0, propagate_preflight=True):
            if fi == f0:
                continue
            enc = mask_to_cvat(to_hw(video_masks))
            out.append({"frame": int(fi), **(enc or {"rle": None})})
    return jsonify({"frames": out, "width": W, "height": H, "count": n})


@app.post("/sg-tools/sam2/from_box")
@app.post("/sg-tools/sam3/from_box", endpoint="from_box3")
def from_box():
    q = request.get_json(force=True)
    ep, boxes = q["episode"], q["boxes"]
    d = frames_dir(ep)
    W, H = Image.open(os.path.join(d, "000000.jpg")).size
    out = []
    with lock, torch.inference_mode(), autocast():
        p = get_predictor()
        state = get_state(p, ep, d)
        for item in boxes:
            f = int(item["frame"])
            x0, y0, x1, y1 = item["box"]
            p.clear_all_points_in_video(state)
            _, _, _low, video_masks = p.add_new_points_or_box(
                state, frame_idx=f, obj_id=1, box=np.array([[x0 / W, y0 / H, x1 / W, y1 / H]], dtype=np.float32))
            enc = mask_to_cvat(to_hw(video_masks))
            out.append({"frame": f, **(enc or {"rle": None})})
    return jsonify({"frames": out, "width": W, "height": H})


@app.post("/sg-tools/sam3/refine")
def refine():
    """Mask refinement by clicks on one frame.
    mode "edit" (default, a mask is given): each click is a part that SAM3 segments under the point; label 1 adds it
    to the mask, label 0 subtracts it; the rest of the mask is unchanged. mode "scratch" or no mask: the object from the points only.
    {"episode", "frame", "mode", "mask": {rle,left,top,width,height} | null, "points": [[x, y], ...], "labels": [1, 0, ...]}"""
    q = request.get_json(force=True)
    ep, f, m = q["episode"], int(q["frame"]), q.get("mask")
    pts, lbl = q.get("points") or [], [int(v) for v in (q.get("labels") or [])]
    mode = q.get("mode", "edit")
    d = frames_dir(ep)
    W, H = Image.open(os.path.join(d, "000000.jpg")).size
    cur = None
    if m:
        cur = np.zeros((H, W), bool)
        cur[m["top"]:m["top"] + m["height"], m["left"]:m["left"] + m["width"]] = rle_decode(m["rle"], m["width"], m["height"])
    if not pts:
        enc = mask_to_cvat(cur) if cur is not None else None
        return jsonify({"frame": f, **(enc or {"rle": None})})
    rel = lambda ps: np.array([[x / W, y / H] for x, y in ps], dtype=np.float32)
    with lock, torch.inference_mode(), autocast():
        p = get_predictor()
        state = get_state(p, ep, d)

        def segment(points, labels):
            p.clear_all_points_in_video(state)
            _, _, _low, vm = p.add_new_points_or_box(state, frame_idx=f, obj_id=1, points=rel(points),
                                                     labels=np.array(labels, dtype=np.int32))
            return to_hw(vm)

        if cur is None or mode == "scratch":   # from scratch: the object from the points only (all points together, as in SAM)
            out = segment(pts, lbl)
        else:                                  # edit by parts: the part under a click is added / subtracted
            # (passing the mask as a prompt together with the clicks made a mask stuck to a neighbour worse in 55 % of
            #  119 test cases; editing by parts never did)
            out = cur.copy()
            for (x, y), l in zip(pts, lbl):
                part = segment([[x, y]], [1])
                out = (out | part) if l == 1 else (out & ~part)
        enc = mask_to_cvat(out)
    return jsonify({"frame": f, **(enc or {"rle": None})})   # width/height here are the size of the mask box, not of the frame


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8000, threaded=True)
