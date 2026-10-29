# SG-Verify user guide

This guide is for reviewers: people who open episodes (videos) one after another, check the automatic annotation
(boxes, masks and the scene graph of every frame), fix what is wrong and give a verdict.
For installation and data preparation see the [README](../README.md).

- [1. Signing in](#1-signing-in)
- [2. The task list](#2-the-task-list)
- [3. The review screen](#3-the-review-screen)
- [4. Scene graph panel: every control](#4-scene-graph-panel-every-control)
- [5. Keyboard shortcuts](#5-keyboard-shortcuts)
- [6. How to fix typical problems](#6-how-to-fix-typical-problems)
- [7. Good to know](#7-good-to-know)

## 1. Signing in

Type your name (Latin letters, digits, `.`, `_`, `-`; at least 3 characters) and press **Next**. A name that does
not exist yet is registered automatically, so a new reviewer needs nothing else. Use the same name every time:
your verdicts and the "in progress" marks are stored under it.

The administrator signs in with a password: click **Sign in with a password (admin)** under the form.
(If the deployment has no team password configured, the form always asks for a password.)

## 2. The task list

One task = one episode. Open **Tasks** (or a project) to see them.

| element | what it means |
|---|---|
| green badge **verified · name** | the episode was checked and accepted by *name* |
| red badge **goal mismatch · name** | *name* decided that the video does not match its goal; the episode is set aside |
| yellow badge **in progress · name** | *name* opened the episode and has not given a verdict yet; **Verified** skips it for other reviewers |
| no badge | not checked yet |
| **Filter → Quick filters: Verified / Goal mismatch / Not checked** | show only episodes with this state |
| default order | not checked first, then goal mismatch, then verified; by name inside each group |

Click a task and then its job to open the review screen. Usually you open only the first episode this way:
after each verdict the next unchecked episode opens by itself.

## 3. The review screen

```
+---------------------------------------------------------------------------------------------+
| top bar: Menu  Save  Undo/Redo  |  player: << < [frame] > >>  |  filters, fullscreen ...     |
+------+-----------------------------+--------------------------------------+-----------------+
| CVAT | SG-Verify scene graph panel |                                      | CVAT objects    |
| tool | - goal, verdict, objects    |            video frame               | sidebar         |
| bar  | - buttons, object tools     |       (boxes and masks drawn)        | (Objects,       |
|      | - graph of this frame       |                                      |  Labels,        |
|      | ======= drag bar =======    |                                      |  Issues)        |
|      | - timeline of all edges     |                                      |                 |
+------+-----------------------------+--------------------------------------+-----------------+
```

- **Objects** are the CVAT labels of the episode (roles such as `obj1` or `cup`) with a human-readable `name`.
  An object has a box track and/or masks. In the graph one object = one node.
- **Edges** are CVAT tags `relation` (a state between two objects, e.g. *cup on table*) or `action`
  (*gripper holds cup*), one tag per frame. An edge to "none" is a property of a single object (*box open*) and is
  drawn as a small chip under the object's node.
- The **goal** (or the scene description) and the object list come from the task guide.
- Everything you change is ordinary CVAT annotation: **Save** (Ctrl+S) stores it, **Undo** (Ctrl+Z) reverts it.
  The verdict buttons save automatically.

The CVAT toolbar on the left and the objects sidebar on the right are standard CVAT; see the
[CVAT manual](https://docs.cvat.ai/docs/) for them. The rest of this guide describes the SG-Verify panel.

## 4. Scene graph panel: every control

### 4.1 Header

| control | what it does |
|---|---|
| **Goal: …** | the goal of the episode (or a scene description) |
| **edit** (next to the goal) | change the goal text; it is saved in the task guide |
| **verified** / **goal mismatch** badge | the verdict of this episode, if any |
| **unmark** | remove the verdict: the episode becomes "not checked" again |
| object list (colored dots) | every object of the episode: color, **name** and (role) |

### 4.2 Buttons

| button | what it does |
|---|---|
| **Add relation** | add a relation between two objects: click the source node, then the target node (the same node twice = a property of one object). A small window asks for the value (start typing: values from the dataset vocabulary are suggested) and the frames (see *scope* below), then **Add** |
| **Add action** | the same for actions. Shown only if the project has the `action` tag label |
| **Unite objects** | two nodes are the same object (the tracker split it): click the node to **keep** (its name stays), then the node to merge into it, confirm. Boxes and masks of the kept object stay; where it has none, those of the other one are used. All edges of the merged object move to the kept one; edges between the two are removed. The node caption becomes `role1 & role2` |
| **Goal mismatch** | verdict "the video does not match its goal": saves, sets the verdict and opens the next unchecked episode |
| **Verified** (Ctrl+Q) | verdict "checked and correct": saves, sets the verdict and opens the next unchecked episode of the project (by name; episodes in progress by other reviewers are skipped). If nothing is left, a message says so |

The grey line under the buttons is a **hint**: it tells what the next click will do (e.g. "click the target node").

### 4.3 Object tools

The tools work on the **selected object**. Select it by clicking its box or mask on the frame, or by clicking its
node in the graph (this also works on frames where the object has no box or mask). The line shows
`name (role, mask, box)`; **×** clears the selection. The same tools also appear next to the cursor when you
**right-click an object on the frame** (to the right of the CVAT object card).

| control | what it does |
|---|---|
| **frames _from_ – _to_** | the frame range the tools below work on. When you select an object it is set to *current frame … last frame* |
| **Interpolate** | keeps the boxes on the first and the last frame of the range and rebuilds the frames in between linearly (removes the keyframes inside the range) |
| **Copy this box** | puts the box of the current frame on every frame of the range (for objects that do not move) |
| **Copy this mask** | puts the mask of the current frame on every frame of the range, replacing the masks of this object there |
| **Track with SAM3** | tracks the mask of the current frame to the far end of the range with SAM3 (if the range ends at the current frame, it tracks backwards to the range start). The masks and boxes of this object on those frames are replaced |
| **Refine with SAM3** | fixes the mask on the current frame by clicks (see 4.4). Also creates a mask from nothing when the object has no mask on this frame |
| **Masks from boxes** | on frames of the range where the object has a box but no mask, SAM3 builds the mask from the box |
| **Hide box** | the object left the frame: marks the box as *outside* on the whole range (the track itself is kept) |
| **Show box** | the object is back: clears *outside* on the range (shown instead of Hide box when the box is hidden) |
| **Delete masks** | deletes the masks of this object on the range |

Track, Refine and Masks from boxes need the tracking service. If it is not running, these buttons are hidden and
the panel says *tracking service not connected*.

### 4.4 Refine with SAM3 (mask by clicks)

1. Select the object and press **Refine with SAM3**.
2. Click on the frame: **left click** adds the part under the cursor, **right click** removes the part under the
   cursor, a click on an existing point deletes that point. After every click a preview of the new mask is shown
   (the old mask is dimmed).
3. Choose the mode:
   - **edit parts** (default when there is a mask): each click adds or cuts one part; the rest of the mask stays;
   - **from scratch**: the old mask is ignored, the object is segmented from your points only (use it when the
     mask is on a completely wrong object).
4. **Apply (Enter)** (or N) replaces the mask on this frame and updates the box; **Cancel (Esc)** leaves everything
   as it was. Moving to another frame cancels the refinement.

Then use **Track with SAM3** to carry the corrected mask through the video.

### 4.5 The graph of the current frame

- A **node** is an object: bold name, role below. A pale dashed node has no box or mask on this frame (or its box
  is hidden). Every object of the episode has a node on every frame, so the picture keeps its shape while you play
  the video; resizing the panel only zooms it.
- Node rows come from the dataset (`level` of each object); without levels the nodes go two (three for more than four
  objects) per row in the order of the object list.
- A **dashed** arrow is a relation, a **solid** arrow is an action; the label on the arrow is the value.
- A **chip under a node** is a property of that object (an edge to "none", e.g. *open*): dashed border = relation,
  solid border = action. Click it to edit it like an edge.
- **Click a node** to select the object for the object tools. **Double-click a node** to rename the object: the new
  name is written to all its boxes and masks on all frames and to the task guide.
- **Click an arrow, its label or a chip** to edit the edge:

| control | what it does |
|---|---|
| value field | the new value (suggestions from the dataset vocabulary) |
| **this frame** | apply to the current frame only |
| **this run** | the current frame and the neighbouring frames where the same edge continues without a break |
| **all frames** | every frame of the episode that has this edge |
| **frames** + *from / to* | frames of the given range (inclusive) |
| **Apply** | change the value on the chosen frames |
| **Delete edge** | delete the edge on the chosen frames |
| **Cancel** | close without changes |

Under the graph: `frame N · K edges` and the legend.

### 4.6 The timeline

One row per edge (value in bold, `subject → object` below, colored by the subject); one cell per frame. A filled
cell means the edge exists on that frame (actions are drawn darker than relations); the current frame is outlined.

| action | result |
|---|---|
| click a cell or a frame number | go to that frame |
| click the **row name** | start re-marking the frames of this edge |
| in marking mode: **left click** a cell | start of a segment (green mark) |
| in marking mode: **right click** a cell | end of a segment (red mark) |
| in marking mode: click a mark again | remove the mark |
| in marking mode: move the mouse over cells | the frame follows the mouse, so you can see where the edge should start and end |
| **✓ Apply (N)** (or Enter) | the edge now exists exactly on the marked segments (a start without an end runs to the last frame) |
| **Cancel (Esc)** | leave the edge as it was |
| **+ new relation / action** | a new edge: choose relation or action, the subject, the object (or "— (state of one object)") and the value, press **Mark frames** and mark the segments as above |

### 4.7 The panel itself

- The icon at the top left of the panel collapses / expands it.
- Drag the right edge of the panel to make it wider or narrower (the width is remembered).
- The graph and the timeline have their own areas. Drag the horizontal bar between them to give one of them more
  height (half and half by default, remembered). The graph always fits its area, so the timeline does not move when the
  graph changes from frame to frame; a long timeline scrolls inside its area.
- The selected object is slightly highlighted on the frame all the time.

## 5. Keyboard shortcuts

| keys | where | action |
|---|---|---|
| **Ctrl+Q** or **Ctrl+Enter** | review screen | Verified + open the next episode (works with any keyboard layout) |
| **N** or **Enter** | timeline marking / Refine | apply |
| **Esc** | timeline marking / Refine | cancel |
| Ctrl+S | CVAT | save |
| Ctrl+Z / Ctrl+Shift+Z | CVAT | undo / redo |
| D / F | CVAT | previous / next frame |
| Space | CVAT | play / pause |
| F1 | CVAT | list of all CVAT shortcuts |

The SG-Verify shortcuts do not work while you type in an input field.

## 6. How to fix typical problems

**The object left the frame (but its box is still drawn).** Select the object, set *from* to the first frame where it
is gone and *to* to the last such frame, press **Hide box**; if there are masks there, press **Delete masks** too.
When it comes back, the same with **Show box**.

**The tracker split one object into two.** Two nodes are the same thing (e.g. `cup` and `obj4`): **Unite objects**,
click the node to keep, click the other one, **Unite**.

**The mask is stuck to a neighbouring object.** Go to the first frame where it happens, select the object,
**Refine with SAM3**, keep **edit parts**, right-click on the neighbour's part (and left-click on missing parts of
your object), **Apply**. Then **Track with SAM3** to the end of the range.

**The mask is on a completely wrong object.** Select the object (click its node), **Refine with SAM3**, switch to
**from scratch**, left-click on the right object, **Apply**, then **Track with SAM3**.

**The object is in the graph but has no mask at all.** Click its node (the tools work even without a mask on the
frame), **Refine with SAM3**, click on the object, **Apply**, **Track with SAM3**. If it has boxes, **Masks from boxes**
is faster.

**The object is missing from the graph.** If the dataset was converted with spare labels (`extra1`, `extra2`, …),
draw a box with the CVAT rectangle tool using a spare label; the object appears as a node. Double-click the node to
give it a name, then build masks with **Refine** + **Track** or **Masks from boxes**.

**The object appears in the middle of the video.** Go to the first frame where it is visible, create or fix its mask
there (**Refine**), then **Track with SAM3** to the end; the track is extended automatically.

**Add a relation that holds only on part of the video.** On the timeline press **+ new relation / action**, choose the
objects and the value, **Mark frames**, left-click the first frame of the segment, right-click the last one, press N.

**A relation has the wrong frames.** Click the row name on the timeline, set the start (left click) and end (right
click) marks again, press N.

**A relation has the wrong value on part of the video.** Go to a frame of that part, click the arrow, type the new
value, choose **this run** (or **frames** with a range), **Apply**.

**A static object's box jitters.** Select it on a good frame, set the range, **Copy this box** (or **Copy this mask**).

**The box drifts between two good frames.** Set *from* and *to* to the two good frames, **Interpolate**.

**The goal text is wrong but the video is fine.** **edit** next to the goal, fix the text. If the video really does
something else, press **Goal mismatch** instead.

**Everything is correct.** Press **Verified** (Ctrl+Q); the next episode opens.

**You made a mistake.** Ctrl+Z undoes annotation changes (also those made by the panel buttons) until you leave the
episode. A verdict can be removed with **unmark**.

## 7. Good to know

- **Open an episode in one browser tab only.** Two tabs of the same episode save over each other and create duplicate
  masks and edges.
- The verdict buttons save the episode first; if saving fails, no verdict is set and a message tells you to press Save.
- Tracking takes a few seconds per episode (the first request after the service starts also loads the model).
- This version is tested on videos of about 30 frames. Much longer videos work but the timeline cells get very
  narrow and the panel gets slower (see the roadmap).
