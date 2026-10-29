# SG-Verify

SG-Verify is a tool for reviewing and correcting automatic annotations of videos: object boxes, segmentation masks
and **temporal scene graphs** (relations and actions between objects on every frame). It is built on top of
[CVAT](https://github.com/cvat-ai/cvat) v2.75.0: CVAT stores the data and draws the frames, and SG-Verify adds a scene
graph panel, a timeline of all relations, SAM3-based mask tracking and a simple review workflow
(verdicts, automatic jump to the next unchecked episode).

![SG-Verify demo](docs/images/demo-previous-version.gif)

*Recording of the previous version (September 2026): the tracking button was still "Track with SAM2", and
"Refine with SAM3" did not exist yet. A new recording will follow.*

**Status: v0.1, a first public version.** It is used for real review work on episodes of about 30 frames.
Longer videos work but are not optimized yet; see [ROADMAP.md](ROADMAP.md).

## What SG-Verify adds to CVAT

Plain CVAT already has box interpolation, *Propagate* (Ctrl+B), the *outside* flag, *Remove annotations* over a
frame range, and a point-based SAM interactor for a single frame. SG-Verify adds:

- **Scene graph panel** next to the frame: the graph of the current frame (objects = nodes, relations and
  actions = arrows); add, edit or delete an edge on one frame, on its continuous run, on a frame range or on all
  frames; rename an object on all frames; **unite** two objects that the tracker split.
- **Timeline**: one row per relation or action, one cell per frame; jump to a frame, re-mark the segments of an
  edge with two clicks, add a new edge directly as segments.
- **Object tools bound to the selected object and a frame range**: interpolate, copy a box or a mask to a range,
  hide the box where the object left the frame, delete masks.
- **SAM3 video tracking of masks** (a separate GPU service): track a mask through the video, build masks from
  boxes, fix a mask by clicks ("add this part / remove that part") and carry the fix through the video.
- **Review workflow**: *Verified* / *Goal mismatch* verdicts stored in the task, "in progress" marks, badges and
  quick filters in the task list, Ctrl+Q = verified + open the next unchecked episode.
- **Name-only sign-in** for a team on a closed network: a reviewer types a name and starts working.
- **Data tools**: a documented episode folder format, a converter to CVAT, an uploader and an exporter back to the
  same format.

Every button is described in the [user guide](docs/USER_GUIDE.md).

## How it works

```
browser ── traefik (127.0.0.1:PORT) ──┬── /api, /admin, /static ──> CVAT server (stock cvat/server:v2.75.0 image)
                                      ├── /sg-tools ──────────────> SAM3 tracking service (optional, GPU)
                                      └── everything else ────────> SG-Verify UI (CVAT UI + patch)
```

- The **UI** is the CVAT v2.75.0 web client with a small patch: the scene graph panel
  (`ui/scene-graph-side-bar/`), name-only sign-in, verdict badges and filters in the task list, the SG-Verify
  name instead of the CVAT logo, and a few CVAT menu items hidden (cloud storages, requests, models, analytics,
  organizations). The full diff is `patch/cvat-v2.75.0-sg-verify.patch`.
- The **server** is the stock CVAT server image with one settings file (`deploy/sg_verify.py`).
- **Data model in CVAT**: one task per episode. Objects are labels (with a text attribute `name`) with a box track
  and masks; relations and actions are per-frame tags `relation` / `action` with attributes
  `subject`/`relation`/`object` (`actor`/`action`/`object`). The goal and the object list are stored in the task
  guide in a `<!-- sg-meta {...} -->` block; the verdict is the task *subset*, the reviewer is the task *assignee*.
  Details: [docs/DATA_FORMAT.md](docs/DATA_FORMAT.md).
- The **tracking service** reads the frames of an episode from `packages/<task name>/frames.zip` on the server
  (the same zip that was uploaded to CVAT; the uploader puts it there).

## Requirements

- Linux with Docker 24+ and Docker Compose v2.
- About 6 GB of disk for the CVAT images plus your data; 8 GB of RAM to build the UI image.
- For tracking: an NVIDIA GPU with at least 8 GB of memory (tested on an RTX 3060 12 GB), NVIDIA driver 535+
  (CUDA 12), the NVIDIA Container Toolkit, and about 10 GB more disk for the tracking image.
- Python 3.8+ with `numpy`, `Pillow` and `requests` for the data tools.

## Installation

```bash
git clone https://github.com/yuriypotekhin/SG-Verify.git
cd SG-Verify
cp deploy/.env.example deploy/.env      # edit: port, project name, ...
deploy/install.sh                        # builds the UI image (5-10 min) and starts the stack
```

`install.sh` clones CVAT v2.75.0 into `build/cvat`, applies the patch, builds the UI image, runs a pre-flight check
of the compose file (`deploy/check_compose.sh`) and starts the stack. On the first start the CVAT server needs a few
minutes to prepare its database. Then create the administrator account once:

```bash
cd deploy && docker compose exec cvat_server bash -c 'python3 ~/manage.py createsuperuser'
```

The UI listens on `127.0.0.1:${SG_VERIFY_PORT}` only (8080 by default). From your computer use an ssh tunnel:

```bash
ssh -L 8080:127.0.0.1:8080 <server>      # then open http://localhost:8080
```

Other useful commands (run in `deploy/`): `docker compose ps`, `docker compose logs -f cvat_server`,
`docker compose stop` / `start`, `./backup_db.sh` (database dump; put it into cron).
**Never remove the compose volumes** (`docker compose down` with the volumes flag): that deletes the database.

The stack does not use `container_name`, fixed network names or traefik docker labels, so it can run next to
another CVAT on the same host (give it its own `COMPOSE_PROJECT_NAME` and port).

### Sign-in and roles

By default reviewers **sign in with a name only**: `install.sh` generates a team password, stores it in
`deploy/.env` and builds it into the UI; a new name is registered automatically. Anyone who can open the page can
therefore sign in, so keep the port on 127.0.0.1 (as configured) or behind your own authentication.
New users get the `admin` role (`SG_VERIFY_DEFAULT_ROLE`), because reviewers set verdicts in tasks they do not own.
To get the usual CVAT username + password form, set `SG_VERIFY_NAME_ONLY=0`, leave `SG_VERIFY_SHARED_PASSWORD`
empty and rebuild the UI image.

### Tracking service (SAM3)

The *Track*, *Masks from boxes* and *Refine* buttons need the SAM3 service. Without it SG-Verify works, and these
buttons are hidden.

1. **Weights.** SAM3 weights are distributed by Meta under the SAM license and are **not** part of this repository.
   Request access to [facebook/sam3 on Hugging Face](https://huggingface.co/facebook/sam3), download the checkpoint
   `sam3.pt` and put it at `deploy/models/sam3.pt` (or set `SG_VERIFY_MODELS_DIR` in `deploy/.env`).
2. **SAM3 code.** `install.sh --gpu` clones [facebookresearch/sam3](https://github.com/facebookresearch/sam3) into
   `sam-service/sam3` and builds the image `sg-verify/sam-service` from `sam-service/Dockerfile`.
3. **Start:** `deploy/install.sh --gpu` (or `docker compose --profile gpu up -d` in `deploy/`).
   `SG_VERIFY_GPU` selects the GPU; `SG_GPU_MEMORY_GB` caps the memory of the service when the GPU is shared.
4. **Frames:** the service reads `deploy/packages/<task name>/frames.zip`. `tools/upload_episodes.py --packages-dir
   deploy/packages` puts every uploaded episode there.

Check: `curl http://127.0.0.1:8080/sg-tools/health` returns `{"ok": true, "model": "sam3", ...}`.

## Preparing data

Data enters SG-Verify as **episode folders** (full description: [docs/DATA_FORMAT.md](docs/DATA_FORMAT.md)):

```
my_episode/
  frames/frame_000000.png ...          frames (or frames.zip, or a video file)
  masks/<object>/frame_000000.png ...  optional masks per object (0/255 PNG)
  graph.json                           objects and edges with their frames
  meta.json                            the goal of the episode (optional)
```

`graph.json` lists the objects (`id`, `name`, optional `roles`, optional `level` = row in the graph layout) and the
edges (`type`: relation or action, `subject`, `predicate`, `object` or null, `frames` or `intervals`). To bring your
own dataset in, write a small script that produces such folders; everything else works unchanged.

```bash
pip install numpy Pillow requests
# 1. a synthetic demo episode (no data licenses involved), or your own episode folders
python3 tools/make_demo_episode.py --out data/episodes --name demo_episode --frames 30
# 2. episode folders -> CVAT packages (frames.zip, labels.json, annotations.xml, guide.md)
python3 tools/to_cvat.py data/episodes data/packages --all
# 3. upload into a project (created with the labels of the packages if it does not exist)
export CVAT_URL=http://127.0.0.1:8080 CVAT_USER=<admin> CVAT_PASSWORD=<password>
python3 tools/upload_episodes.py data/packages --project "My review" --packages-dir deploy/packages
# 4. review in the browser (docs/USER_GUIDE.md)
# 5. export the reviewed episodes back to episode folders (+ verdict and reviewer in meta.json)
python3 tools/export_episodes.py "My review" data/reviewed --packages-dir deploy/packages
```

`to_cvat.py --spare N` (default 2) adds empty labels `extra1..N`, so that a reviewer can add an object the
pipeline missed.

## Development

The UI can be developed against a running stack with the webpack dev server (changes apply on save):

```bash
cd build/cvat && corepack enable && yarn install
CVAT_SRC=$PWD API_URL=http://127.0.0.1:8080 PORT=3000 ../../deploy/start_ui_dev.sh   # open http://localhost:3000
```

To update the patch after changing the CVAT clone: `git -C build/cvat diff v2.75.0 > patch/cvat-v2.75.0-sg-verify.patch`
(include new files with `git add -N` first).

## Repository layout

```
README.md, ROADMAP.md, LICENSE, NOTICE
docs/          USER_GUIDE.md (every button), DATA_FORMAT.md, images/
patch/         cvat-v2.75.0-sg-verify.patch  - all changes to the CVAT v2.75.0 client
ui/            scene-graph-side-bar/         - the panel source (also contained in the patch), for reading
deploy/        docker-compose.yml, .env.example, install.sh, check_compose.sh, sg_verify.py, traefik/,
               start_ui_dev.sh, backup_db.sh
sam-service/   server.py, Dockerfile         - SAM3 tracking service
tools/         make_demo_episode.py, to_cvat.py, upload_episodes.py, export_episodes.py (+ shared modules)
```

## License

SG-Verify is released under the [MIT License](LICENSE). It modifies and includes parts of CVAT, which is also
MIT-licensed; see [NOTICE](NOTICE). SAM3 is not included: its code and weights are subject to Meta's license.
The frames in the demo recording are from the ShareRobot dataset and are shown for illustration only.
