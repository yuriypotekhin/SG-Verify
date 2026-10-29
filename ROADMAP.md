# Roadmap

v0.1 is the version used for real review work on episodes of about 30 frames. Planned next, roughly in order:

## Videos of any length
- Timeline: a window of frames with zoom (−/+/whole episode, Ctrl+wheel), scrolling, an overview strip of the whole
  video, frame-number ticks chosen by the available width; rows drawn as segments instead of one cell per frame;
  cells of a fixed maximum width on very short videos.
- Speed on hundreds of frames: build the index of edges once and update only the current frame on a frame step
  (now the timeline is rebuilt from all frames on every step); remove the quadratic pass in *Unite objects*; use the
  index in rename, segment marking and "all frames" edits.
- Test and report on 5, 30, 300 and 1000-frame episodes.

## Graph layout
- Drag nodes with the mouse; the layout is saved per episode (in the task guide) and is the same on every frame.
- A constant set of nodes for the whole episode and a constant scale (now nodes that appear on some frames only and
  edge labels change the scale between frames).
- *Reset layout*.

## Objects
- A **+ Object** button: add an object the pipeline missed (take a spare label or create a new label through the
  CVAT API), then segment it with Refine and Track.

## Several reviewers and tabs
- Block a second browser tab of the same episode (now two tabs can create duplicate masks and edges).
- A banner when the episode is in progress by another reviewer, with "take over" / "next episode".
- Check on save that nobody else changed the episode since it was opened.
- Next unchecked episode: search beyond the first 50 tasks; a switch "open the next episode after a verdict".

## Tracking service
- Take frames from the CVAT API by job id instead of `packages/<task name>/frames.zip`.
- One service for SAM2 (public weights) and SAM3, chosen by a setting; neutral endpoint names
  (`/sg-tools/track`, ...); clean up the temporary frame cache; optional authentication.

## Sign-in and deployment
- Sign-in mode set at run time (not baked into the UI image).
- Optional analytics services (ClickHouse, Vector, Grafana) as a compose profile.
- Prebuilt images.

## Documentation and examples
- A new demo recording of v0.1; screenshots for every part of the guide.
- A Russian translation of the user guide.
- Example converters for public video scene-graph datasets.
- Unit tests for the data tools and an end-to-end test of upload → review → export.
