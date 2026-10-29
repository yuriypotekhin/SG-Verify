# SG-Verify data format

SG-Verify reviews **video scene graphs**: for every frame of an episode, the objects (box and mask) and the
edges between them (relations such as `cup on table`, actions such as `gripper grab cup`). Data enters and
leaves the tool in one plain folder format, the **episode folder**. The tools in `tools/` convert it to
and from CVAT:

```
episode folder ──to_cvat.py──> CVAT package ──upload_episodes.py──> CVAT task ──(review in the browser)──>
CVAT task ──export_episodes.py──> episode folder (+ verdict, reviewer)
```

The exported folder is valid input for `to_cvat.py` again, so a dataset can go through several review
rounds. To bring your own data in, write a small script that produces episode folders. The rest of
the pipeline then works unchanged.

All tools need Python 3.8+ with `numpy` and `Pillow`. `upload_episodes.py` and `export_episodes.py` also
need `requests`, and `ffmpeg` is needed only for video input.

---

## 1. Episode folder

```
<episode>/                      folder name = episode id = CVAT task name
  frames/frame_000000.png ...   frames (PNG or JPEG), or frames.zip, or a video file (see 1.1)
  masks/<object>/frame_000000.png ...   optional per-object masks (see 1.2)
  graph.json                    objects and edges (see 1.3)
  meta.json                     goal and other episode facts (see 1.4), optional
```

**Frame numbers** are used everywhere (mask file names, `boxes`, edge `frames`). They count from 0 in the
order of the frames: the natural sort of the file names in `frames/` (`f2.png` comes before `f10.png`), or the
decoding order of the video. All frames of an episode must have the same size. Episodes can have any
length, from one frame up.

### 1.1 Frames

`to_cvat.py` uses the first source it finds:

1. `--video FILE` on the command line (single episode only);
2. `frames/*.png|*.jpg|*.jpeg`;
3. `frames.zip` with images inside (any folder structure; natural sort of the member names);
4. a video file in the episode folder (`video.*` is preferred, otherwise the first `*.mp4|avi|mov|mkv|webm`).
   Every decoded frame is kept (`ffmpeg -fps_mode passthrough`). Frames are stored as JPEG, or as PNG
   with `--video-format png`.

In the package, the frames are renamed `frame_%06d.<ext>`, so the natural sort in CVAT, the plain sort in the
SAM service and the frame numbers all agree.

### 1.2 Masks

`masks/<object>/frame_%06d.png`: a single-channel or RGB image of the frame size. Any non-zero pixel belongs
to the object. `<object>` is the object's `id`, or one of its roles. If a frame has no file, or the file is
all zero, the object has no mask on that frame. For frames whose files are not called `frame_%06d`, a
mask may also be named after the frame file (`masks/cup/<frame stem>.png`).

A mask is the **visible** part of the object. Masks of different objects may overlap.

### 1.3 graph.json

```json
{
  "objects": [
    {"id": "gripper", "roles": ["gripper"], "name": "robot gripper", "level": 0},
    {"id": "cup",     "roles": ["cup"],     "name": "red cup",       "level": 1},
    {"id": "table",   "role": "table",      "name": "wooden table",  "level": 2,
     "boxes": {"0": [12, 163, 302, 236], "1": [12, 163, 302, 236]}},
    {"id": "box",     "roles": ["box", "target"], "name": "cardboard box", "level": 2}
  ],
  "edges": [
    {"type": "action",   "subject": "gripper", "predicate": "grab",    "object": "cup",  "frames": [7, 8, 9]},
    {"type": "relation", "subject": "cup",     "predicate": "on",      "object": "table", "intervals": [[0, 9]]},
    {"type": "relation", "subject": "box",     "predicate": "open",    "object": null,   "frames": [9, 10, 11]}
  ],
  "ontology": {"states": ["on", "inside", "holding", "open"], "actions": ["reach_for", "grab", "place"]}
}
```

**objects[]**

| field | required | meaning |
|---|---|---|
| `id` | recommended | Object id. Edges and the `masks/` folder refer to it. Default: the role. |
| `role` / `roles` | no | CVAT label of the object: `role`, otherwise the first item of `roles`. Further `roles` are other roles of the same object (for example, `["initial_support", "target"]` when the object is both). They are kept as sg-meta `merges`, see 2.4. Without a role the object gets `obj1`, `obj2`, ... |
| `name` | recommended | Human-readable name shown in the graph, e.g. `red cup`. Default: the id. |
| `level` | no | Row of the node in the graph panel layout, 0 = top. |
| `boxes` | no | `{"<frame>": [x0, y0, x1, y1]}` in pixels: top-left origin, `x1`/`y1` are the right/bottom edges. If this key is present, it **is** the object's box track. If it is absent, a box is computed from the mask on every frame that has one (`x1 = max x + 1`). |
| `entity_id` | no | Id of the object in your own source data. It is passed through to sg-meta and the export unchanged. Default: `id`. |

Role names must be unique within an episode and must not be `relation`, `action` or `none`.
`to_cvat.py --roles-from` chooses the CVAT label:

* `auto` (default): `role`, otherwise `roles[0]`, otherwise `objN`;
* `id`: the object id;
* `index`: always `obj1..objK` in object order. Use it to keep one small label set for a project that mixes
  many different episodes. The original roles are kept as sg-meta aliases, so the panel shows `obj1 & gripper`.

**edges[]**

| field | meaning |
|---|---|
| `type` | `action` becomes an action. Anything else (`relation`, `state`, ...) becomes a relation. |
| `subject` | Object id or role. For an action, this is the actor. |
| `predicate` | Free text, e.g. `on`, `holding`, `grab`. |
| `object` | Object id or role. `null`, `"none"`, `""` or a missing field mean a **unary** edge, i.e. a state of one object such as `box open`. `object == subject` is also treated as unary. |
| `frames` | List of frame numbers where the edge holds. An item may also be a `[start, end]` pair. |
| `intervals` | List of `[start, end]` pairs (inclusive). It may be used instead of `frames` or together with it, and the union of both is taken. |

The two edge kinds mean different things. A **relation** is a state of the scene that you can check on a
single frame (`on`, `inside`, `holding`, `open`). An **action** is something an agent does, which you see
only from motion (`reach_for`, `grab`, `lift`, `place`). An edge on a frame range is one CVAT tag on every
frame of the range. Edges that name an unknown object, have an empty predicate or fall on frames outside
the episode are reported and skipped.

**ontology** (optional; it can also come from `meta.json` or `to_cvat.py --ontology FILE`) is the list of
suggested predicates that the panel offers when an edge is added or renamed. The predicates found in the edges are always
added to it. Predicates stay free text in CVAT, so a reviewer may type a new one.

### 1.4 meta.json

```json
{"episode": "demo_episode", "goal": "Pick up the red cup from the table and put it into the cardboard box."}
```

`to_cvat.py` reads the following keys:

* `goal`: one sentence shown above the graph, which the reviewer checks against the video;
* `ontology`: see 1.3;
* `sg_meta_extra`: a dict passed into the sg-meta block unchanged;
* `n_frames`: if present, it is compared with the number of frames found;
* `episode`: informational only. The folder name is the episode id.

Other keys are ignored. Keep anything you need there, such as source or license.

---

## 2. CVAT package (output of `to_cvat.py`)

```
<packages>/<episode>/
  frames.zip        frame_000000.<ext>, frame_000001.<ext>, ... (stored, not recompressed)
  labels.json       labels of the task (or of the project)
  annotations.xml   "CVAT 1.1" XML: box tracks, masks, tags
  guide.md          task guide with the sg-meta block
```

`annotations.xml` is written last, so a folder that has it is a complete package.
`--skip-existing` relies on this when a long batch is restarted.

### 2.1 labels.json

* One label of type `any` per object role, plus `--spare N` (default 2) empty roles `extra1..extraN`, all with
  a text attribute `name` (the object's name). With a spare role a reviewer can add an object that the
  pipeline missed: they draw its box or mask with label `extra1` and set `name`.
* Tag label `relation` with the attributes `subject` (select: roles), `relation` (text) and
  `object` (select: `none` + roles).
* Tag label `action` with the attributes `actor` (select: roles), `action` (text) and `object` (select: `none` + roles).

```text
[
 {"name": "gripper", "color": "#e6194b", "type": "any", "attributes": [
   {"name": "name", "input_type": "text", "mutable": false, "default_value": "", "values": [""]}]},
 ...
 {"name": "relation", "color": "#000000", "type": "tag", "attributes": [
   {"name": "subject",  "input_type": "select", "mutable": false, "default_value": "gripper",
    "values": ["gripper", "cup", "table", "box", "extra1", "extra2"]},
   {"name": "relation", "input_type": "text",   "mutable": false, "default_value": "", "values": [""]},
   {"name": "object",   "input_type": "select", "mutable": false, "default_value": "none",
    "values": ["none", "gripper", "cup", "table", "box", "extra1", "extra2"]}]},
 {"name": "action", "color": "#808080", "type": "tag", "attributes": [ actor, action, object: like the three above ]}
]
```

### 2.2 annotations.xml

```xml
<annotations>
  <version>1.1</version>
  <track id="1" label="cup" source="file" group_id="2">          <!-- one track per object -->
    <box frame="0" xtl="36" ytl="67" xbr="52" ybr="82" outside="0" occluded="0" keyframe="1" z_order="0">
      <attribute name="name">red cup</attribute>
    </box>
    ...                                                           <!-- a keyframe on every frame with a box -->
    <box frame="23" ... outside="1" ...>...</box>                 <!-- first frame after a run: hide the box -->
  </track>
  <image id="0" name="frame_000000.png" width="160" height="120">
    <mask label="cup" source="file" occluded="0" z_order="0" group_id="2"
          rle="5,6,8,10,5,12,..." left="36" top="67" width="16" height="15">
      <attribute name="name">red cup</attribute>
    </mask>
    <tag label="action" source="file">
      <attribute name="actor">gripper</attribute>
      <attribute name="action">reach_for</attribute>
      <attribute name="object">cup</attribute>
    </tag>
  </image>
  ...
</annotations>
```

* **Object**: one rectangle track of the object's role. The track and all masks of the object share a `group_id`.
* **Mask**: one `<mask>` per object and frame. The RLE format is the one of CVAT (`cvat-core/src/rle-utils.ts`).
  Take the mask's bounding box (`left`, `top`, `width`, `height`), read its pixels row by row, and write the
  lengths of alternating runs, starting with a run of **zeros** (which is 0 if the first pixel is set). In
  the CVAT API, the same mask is `points = runs + [left, top, right, bottom]` with
  `right = left + width - 1` and `bottom = top + height - 1`.
* **Edge**: one `<tag>` on every frame where the edge holds.

`upload_episodes.py` imports this file with format `CVAT 1.1` and `conv_mask_to_poly=false`, so masks stay masks.

### 2.3 guide.md

The task guide (CVAT's "annotation guide") contains:

* `**Episode:**` and `**Goal:**`;
* a table of roles and object names;
* a short checklist for the reviewer (replace it with `--checklist FILE`);
* at the end, the machine-readable **sg-meta** block that the scene-graph panel reads and writes:

```
<!-- sg-meta {"episode": "demo_episode", "goal": "Pick up ...",
  "entities": [{"entity_id": "gripper", "role": "gripper", "name": "robot gripper", "level": 0}, ...],
  "ontology": {"states": ["on", "holding", "open", "inside"], "actions": ["reach_for", "grab", ...]},
  "merges": {"target": "initial_support"}} -->
```

(The block is a single line in the file.)

### 2.4 sg-meta keys

| key | written by | meaning |
|---|---|---|
| `episode` | to_cvat | Episode id = task name. The SAM tracking service finds the frames by it. |
| `goal` | to_cvat; the panel when the goal is edited | Goal sentence. |
| `entities` | to_cvat; the panel when an object is renamed | `[{entity_id, role, name, level?}]`: graph nodes and their order. |
| `ontology` | to_cvat | `{states, actions}`: suggestion lists. |
| `merges` | to_cvat (extra `roles`); the panel ("Unite objects") | `{role: role it belongs to}`: the panel shows both roles as one node. |
| other keys | the panel | Kept by the export in `meta.json` → `sg_meta_extra` and written back by `to_cvat.py`. |

---

## 3. In CVAT

`upload_episodes.py` creates **one task per episode**. The task name is the folder name, and the
task gets its frames, annotations and guide. With `--project NAME`, the project is created if needed,
with the union of the labels of all uploaded packages. If the project already exists, the missing roles
and role values are added to its labels. With `--packages-dir DIR`, the script also copies each
`frames.zip` to `DIR/<task name>/frames.zip`. The SAM tracking service reads the frames from there, so
point it at that folder. Tasks that already exist are skipped.

During the review, the panel stores everything in CVAT itself:

* corrected **boxes** (track keyframes), **masks** (mask shapes) and **edges** (tags);
* object **names** (the `name` attribute and sg-meta `entities`), the **goal**, and **merges** of objects
  that turned out to be one object (sg-meta in the guide);
* the **verdict** in the task's `subset` field: `verified`, `goal_mismatch` or empty (not reviewed yet);
* the **reviewer** in the task's `assignee`. The panel sets the assignee when someone opens an unreviewed
  episode (the episode is then "in progress") and again with the verdict.

---

## 4. Export (output of `export_episodes.py`)

The export has the same layout as the input, so it can be converted again:

```
<out>/<episode>/
  masks/<role>/frame_%06d.png   0/255; one file per object and frame (all-zero if there is no mask),
                                or only non-empty ones with --sparse-masks
  graph.json                    objects and edges
  meta.json                     goal, verdict, reviewer, ...
  frames.zip | frames/          only with --packages-dir DIR (copy) or --download-frames (from CVAT)
<out>/_raw/<episode>.json.gz    with --raw: the raw CVAT task, guide, annotations and frame list
```

**graph.json** after review (shortened; here the reviewer found that the spare object `extra1` was the
table and united the two):

```text
{"objects": [
   {"id": "cup", "roles": ["cup"], "name": "red cup", "level": 1,
    "boxes": {"0": [36.0, 67.0, 52.0, 82.0], "1": [36.0, 67.0, 52.0, 82.0], ...}},
   {"id": "table", "roles": ["table", "extra1"], "name": "wooden table", "level": 2, "boxes": {...}},
   ...],
 "edges": [
   {"type": "relation", "subject": "cup", "predicate": "on", "object": "table",
    "frames": [0, 1, 2, 3], "intervals": [[0, 3]]},
   {"type": "relation", "subject": "box", "predicate": "open", "object": null,
    "frames": [4, 5, 6], "intervals": [[4, 6]]}]}
```

* `id` is the CVAT role. `roles` is the role followed by the roles merged into it. `entity_id` is present
  only when it differs from `id`.
* `name` is the most frequent `name` attribute of the object's boxes and masks. If there is none, the sg-meta
  name is used.
* `boxes` contains the box on **every** frame where the track is visible, with CVAT's linear interpolation
  between keyframes resolved. Objects without a box track have no `boxes`.
* Masks of merged roles are joined into the object's mask. If one label has two masks on the same frame,
  the newest one is kept (this happens when one task is open in two browser tabs). The export prints a warning.
* Each edge is written with both `frames` and `intervals`. They describe the same set of frames.

**meta.json** after review:

```json
{
 "episode": "demo_episode",
 "goal": "Pick up the red cup from the table and put it into the cardboard box.",
 "verdict": "verified",
 "reviewer": "alice",
 "n_frames": 30,
 "frame_size": [320, 240],
 "n_objects": 4,
 "n_edges": 11,
 "ontology": {"states": ["on", "holding", "open", "inside"], "actions": ["reach_for", "grab", "lift", "move", "place", "release"]},
 "cvat": {"task_id": 31, "project_id": 2, "updated_date": "2026-01-01T12:00:00.000000Z"}
}
```

`verdict` is `verified` (everything checked and correct), `goal_mismatch` (the video does not show the goal;
the episode was not checked further) or `""` (not reviewed). `reviewer` is the task assignee. On an episode
without a verdict, it is the person who has it in progress. Filter on `verdict` before you use the data.

---

## 5. End to end

```bash
python3 tools/make_demo_episode.py --out data/episodes --name demo_episode --frames 30
python3 tools/to_cvat.py data/episodes data/packages --all
export CVAT_URL=http://localhost:8080 CVAT_USER=admin; read -s CVAT_PASSWORD; export CVAT_PASSWORD
python3 tools/upload_episodes.py data/packages --project "Demo review" --packages-dir sam_packages
# review in the browser: open the task, fix boxes / masks / edges, press Verified
python3 tools/export_episodes.py "Demo review" data/reviewed --packages-dir sam_packages
```

`sam_packages` is the folder that the SAM tracking service reads. If your CVAT is behind a reverse proxy that
routes by host name and you reach it by another address (an IP, an ssh tunnel), also set
`CVAT_HOST_HEADER=localhost` (or the host name the proxy expects).

## 6. Tool reference

| tool | command line |
|---|---|
| `make_demo_episode.py` | `--out DIR [--name demo_episode] [--frames 30] [--size 640x480] [--seed 0]` |
| `to_cvat.py` | `INPUT OUT [--all] [--ontology FILE] [--spare 2] [--roles-from auto\|id\|index] [--video FILE] [--video-format jpg\|png] [--checklist FILE] [--skip-existing] [--workers N]` |
| `upload_episodes.py` | `PACKAGES [--project NAME] [--packages-dir DIR] [--only NAMES_FILE] [--image-quality 95] [--timeout 3600] [--keep-failed]` |
| `export_episodes.py` | `PROJECT OUT [--only NAMES_FILE] [--packages-dir DIR \| --download-frames] [--sparse-masks] [--raw]` |

`upload_episodes.py` and `export_episodes.py` read these environment variables: `CVAT_URL` (default
`http://localhost:8080`), `CVAT_USER` and `CVAT_PASSWORD` (both required, with no defaults), and
`CVAT_HOST_HEADER` (optional). `NAMES_FILE` lists episode names, one per line; `#` starts a comment.
`sg_format.py` and `cvat_api.py` are shared modules and are not run directly.

**Known limits:** all frames of an episode must have the same size. Box rotation, polygons and
skeletons are ignored by the export. A task is assumed to use all of its frames (no frame step or start
offset).
