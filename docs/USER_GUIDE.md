# SG-Verify user guide

For reviewers: how to check a video, fix its annotation and give a verdict.
Installation and data preparation are in the [README](../README.md).

- [1. Signing in](#1-signing-in)
- [2. Task list](#2-task-list)
- [3. Review screen](#3-review-screen)
- [4. Scene graph panel](#4-scene-graph-panel)
- [5. Keyboard shortcuts](#5-keyboard-shortcuts)
- [6. How to fix typical problems](#6-how-to-fix-typical-problems)
- [7. Good to know](#7-good-to-know)

## 1. Signing in

Type your name (Latin letters, at least 3 characters) and press **Next**. A new name is registered automatically.
Always use the same name: your verdicts are stored under it.

The administrator signs in with a password: **Sign in with a password (admin)** under the form.

## 2. Task list

One task is one video (episode).

| element | meaning |
|---|---|
| green **verified · name** | checked and accepted by *name* |
| red **goal mismatch · name** | the video does not match its goal |
| yellow **in progress · name** | *name* is checking it now; other reviewers skip it |
| no badge | not checked yet |
| **Filter → Quick filters** | show only Verified, Goal mismatch or Not checked |

Unchecked videos come first. Open a task and its job to start; after each verdict the next unchecked video opens by
itself.

## 3. Review screen

```
+------+---------------------------+---------------------------+-------------+
| CVAT | SG-Verify panel           |                           | CVAT        |
| tool | goal, objects, buttons    |        video frame        | objects     |
| bar  | graph of this frame       |                           | list        |
|      | ======== drag bar ======= |                           |             |
|      | timeline of all relations |                           |             |
+------+---------------------------+---------------------------+-------------+
```

- **Objects** have a box and/or a mask on each frame. In the graph one object is one node.
- **Relations** connect two objects (*cup on table*), **actions** too (*gripper holds cup*). A property of one object
  (*box open*) is shown as a small chip under its node.
- Changes are saved with **Save** (Ctrl+S) and undone with **Undo** (Ctrl+Z). The verdict buttons save by themselves.
- The CVAT toolbar on the left and the objects list on the right are standard CVAT, see the
  [CVAT manual](https://docs.cvat.ai/docs/).

## 4. Scene graph panel

### Header

| control | what it does |
|---|---|
| **Goal** | the goal of the video; **edit** changes it |
| **verified** / **goal mismatch** | the verdict, if there is one; **unmark** removes it |
| object list | color, name and role of every object |

### Buttons

| button | what it does |
|---|---|
| **Add relation** | click the first object, then the second one (the same object twice = a property), type the value, choose the frames, **Add** |
| **Add action** | the same for actions |
| **Unite objects** | two nodes are the same object: click the one to keep, then the other one, confirm. Boxes, masks and relations are moved to the kept object |
| **Goal mismatch** | verdict "the video does not match its goal", opens the next video |
| **Verified** (Ctrl+Q) | verdict "checked and correct", opens the next unchecked video |

The grey line under the buttons tells what the next click does.

### Object tools

Select an object: click it on the frame or click its node in the graph. The same tools appear when you
**right-click an object on the frame**.

| control | what it does |
|---|---|
| **frames** from – to | the frames the tools work on |
| **Interpolate** | rebuilds the boxes between the first and the last frame of the range linearly |
| **Copy this box** | puts the box of the current frame on all frames of the range |
| **Copy this mask** | puts the mask of the current frame on all frames of the range |
| **Track with SAM3** | carries the mask of the current frame through the range |
| **Refine with SAM3** | fixes the mask on the current frame by clicks (below); also creates a mask from nothing |
| **Masks from boxes** | builds masks from the boxes where masks are missing |
| **Hide box** / **Show box** | the object left the frame / came back |
| **Delete masks** | deletes the masks of the object on the range |

The SAM3 buttons are hidden when the tracking service is not running.

### Refine with SAM3

1. Select the object and press **Refine with SAM3**.
2. Click on the frame: **left click** adds a part, **right click** removes a part, a click on a point deletes it.
   A preview of the new mask appears after each click.
3. **edit parts** changes the existing mask; **from scratch** ignores it (use it when the mask is on the wrong object).
4. **Apply** (Enter) or **Cancel** (Esc). Then **Track with SAM3** carries the fixed mask through the video.

### Graph

- A **node** is an object. A pale node has no box or mask on this frame.
- A **dashed** arrow is a relation, a **solid** arrow is an action. A **chip** under a node is a property of the object.
- **Click a node** to select the object, **double-click** it to rename the object on all frames.
- **Click an arrow, its label or a chip** to edit it:

| control | what it does |
|---|---|
| value | the new value (suggestions from the dataset) |
| **this frame** / **this run** / **all frames** / **frames** | where to apply: this frame, the continuous run of this relation, all frames, or a range |
| **Apply** / **Delete edge** / **Cancel** | change, delete, close |

### Timeline

One row per relation or action, one cell per frame; filled cells are the frames where it holds.

| action | result |
|---|---|
| click a cell | go to that frame |
| click the row name | re-mark the frames of this relation: **left click** = start, **right click** = end, **N** = apply, **Esc** = cancel |
| **+ new relation / action** | a new relation, marked directly on the timeline |

### Panel layout

- The icon at the top collapses the panel; drag its right edge to change the width.
- Drag the bar between the graph and the timeline to share the height between them.

## 5. Keyboard shortcuts

| keys | action |
|---|---|
| **Ctrl+Q** or **Ctrl+Enter** | Verified and the next video |
| **N** or **Enter** | apply (timeline marking, Refine) |
| **Esc** | cancel (timeline marking, Refine) |
| Ctrl+S / Ctrl+Z | save / undo |
| D / F | previous / next frame |
| Space | play / pause |
| F1 | all CVAT shortcuts |

## 6. How to fix typical problems

**The object left the frame.** Select it, set the frames where it is gone, **Hide box** (and **Delete masks**).

**The tracker split one object into two.** **Unite objects**: the node to keep, then the other one.

**The mask covers a neighbouring object.** On the first bad frame: **Refine with SAM3**, right-click on the
neighbour, **Apply**, then **Track with SAM3**.

**The mask is on the wrong object.** Click the node, **Refine with SAM3**, **from scratch**, click the right object,
**Apply**, **Track with SAM3**.

**The object has no mask at all.** Click its node, **Refine with SAM3**, click the object, **Apply**, **Track with SAM3**.
If it has boxes, **Masks from boxes** is faster.

**The object is missing from the graph.** Draw a box with a spare label (`extra1`, `extra2`, ...) using the CVAT
rectangle tool; the object appears in the graph. Double-click the node to name it, then build its masks.

**A relation holds only on part of the video.** **+ new relation / action** on the timeline, choose the objects and
the value, **Mark frames**, left-click the first frame, right-click the last one, N.

**A relation has the wrong frames.** Click the row name on the timeline, mark the start and the end again, N.

**A relation has the wrong value.** Click the arrow, type the new value, choose **this run**, **Apply**.

**A box jitters or drifts.** **Copy this box** for an object that does not move, **Interpolate** between two good frames.

**The goal text is wrong.** **edit** next to the goal. If the video does something else, press **Goal mismatch**.

**Everything is correct.** **Verified** (Ctrl+Q).

## 7. Good to know

- Open a video in one browser tab only: two tabs of the same video overwrite each other.
- The first tracking request after the service starts takes longer: the model is loading.
- The tool is tested on videos of about 30 frames; long videos work, but the timeline cells become narrow.
