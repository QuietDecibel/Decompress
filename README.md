# Decompress

Find the IP cameras on **your own** network, watch many feeds (and imported videos) at once, let computer
vision detect and track people and the way they move, learn movements you label, and rebuild the
recorded motion as an animated armature in Blender.

Also in this repo is the **Claude Progress Hub** (`hub/`), a standalone dashboard that this project and any
other Claude project can report progress to.

| Area | What works today |
|---|---|
| Discovery | ONVIF WS-Discovery + RTSP/port probe of private subnets only (≤1024 hosts), vendor stream-URL templates. No password guessing: you enter your camera's own credentials. |
| Multi-view | Grid of 1×1 to 4×4 tiles: RTSP, HTTP/MJPEG, webcams, imported files. Each tile shows live skeletons, track IDs, trails and movement labels. Credentials are redacted in the UI/API. |
| Queue | Drag & drop videos into a persistent queue (reorder, cancel, resume after restart). Each job writes an annotated `.mp4` and a `.motion.json`. |
| Vision | Backends: **MediaPipe** (2D + metric 3D, multi-person), **YOLO-pose**, **YOLO-det** (people + animals as boxes), **HOG** fallback. A multi-person tracker gives stable IDs. Movement labels: walking / running / crouching / standing / arms raised. |
| Training | Label clips → `python -m decompress train labels.json` → the k-NN movement classifier is used live in every tile. |
| Blender | `blender/import_motion.py` builds keyframed joint empties + a 14-bone armature (Copy Location / Stretch To), with optional bake to plain keyframes. Tested with bpy 5.2. |

Full roadmap with status: [PLAN.md](PLAN.md).

## Quick start

```bash
pip install -r requirements.txt
```

```bash
python -m decompress serve            # open http://127.0.0.1:8000
```

MediaPipe downloads its pose model (~9 MB) on first use. On headless Linux it also needs `libegl1` and `libgles2`.

Other commands:

```bash
python -m decompress discover --subnet 192.168.1.0/24
```

```bash
python -m decompress --backend mediapipe analyze my_clip.mp4      # writes to decompress_data/outputs/
```

```bash
python -m decompress train labels.json
```

## Blender

```bash
blender --python blender/import_motion.py -- decompress_data/outputs/<job>-clip.motion.json --bake
```

Options: `--track N` imports one person, `--smooth 5` sets the smoothing window (frames), `--bake` bakes the motion to keyframes.
Use the MediaPipe backend for true 3D. With 2D-only backends the motion is placed on the camera plane until 2D→3D lifting (P6.4) lands.

## Training your own movements

1. Record motion: click **● Record motion** on a tile, or analyse a file through the queue.
2. Write `labels.json`:
   ```json
   [{"motion": "decompress_data/outputs/ab12-clip.motion.json", "track": 1, "start": 0, "end": 90, "label": "waving"},
    {"motion": "decompress_data/outputs/ab12-clip.motion.json", "track": 1, "start": 120, "end": 200, "label": "jumping"}]
   ```
3. `python -m decompress train labels.json`. New tiles use it automatically, and `POST /api/classifier/reload` updates running tiles.

## Claude Progress Hub

```bash
python hub/server.py                          # dashboard at http://127.0.0.1:8765
```

```bash
python hub/hub_client.py sync plan.json       # push this project's plan
```

```bash
python hub/hub_client.py task decompress P1.4 in_progress --note "GetStreamUri"
```

```bash
python hub/hub_client.py show                 # progress bars in the terminal
```

See [hub/README.md](hub/README.md) for connecting other Claude projects.

## Responsible use

Only scan networks and view cameras you own or are authorised to access. The scanner refuses public
address ranges and large subnets, never tries default or guessed passwords, and binds the web UI to
localhost by default.

## Tests

```bash
pip install -r requirements-dev.txt && python -m pytest -q
```
