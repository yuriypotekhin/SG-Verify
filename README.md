# SG-Verify

Scene-graph verification tool for video datasets, built on top of [CVAT](https://github.com/cvat-ai/cvat).
It lets annotators review and correct automatic video annotations — object boxes, segmentation masks and
**temporal scene graphs** (relations and actions per frame) — in one interface.

**Status:** the code is being prepared for public release; the first version will be published here shortly.

## Features (first release)

- Scene-graph panel next to the video frame: per-frame graph, edit relations and actions, rename and merge objects
- Temporal view of the graph: one row per relation, one cell per frame, interval editing
- Mask tracking with SAM 2 / SAM 3 across frames, masks from boxes, click-based mask refinement
- Review workflow: per-episode verdicts, automatic jump to the next unreviewed episode
- Converters from/to dataset formats; tested on ShareRobot and Charades-based scene-graph data

## License

MIT (CVAT is MIT-licensed; its notice is kept for the modified parts).
