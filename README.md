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
| Training | Label clips → `decompress train labels.json` → the k-NN movement classifier is used live in every tile. |
| Blender | A Blender add-on (`dist/decompress_blender_addon.zip`) builds keyframed joint empties + a 14-bone armature (Copy Location / Stretch To), with optional bake to plain keyframes. Tested with bpy 5.2. |

Full roadmap with status: [PLAN.md](PLAN.md).

## Install, build, test, rebuild

Everything is driven by one script: `build.cmd` / `scripts\build.ps1` on Windows, `scripts/build.sh` on Linux and macOS.
Python 3.10–3.12 is recommended (MediaPipe's supported range).

**Windows** (in `C:\GitHub\Decompress`, from cmd or PowerShell):

```bash
build rebuild
```

**Linux / macOS:**

```bash
./scripts/build.sh rebuild
```

`rebuild` runs the full cycle: **clean → setup → test → build → verify**. You can also run each step on its own:

| Command | What it does |
|---|---|
| `setup` | Creates `.venv` and installs the app in editable mode, plus MediaPipe and the dev tools |
| `test` | Runs the unit tests |
| `build` | Writes `dist/decompress-<ver>-py3-none-any.whl`, the sdist, and `dist/decompress_blender_addon.zip` |
| `verify` | Installs the wheel into a fresh venv and smoke-tests the commands, web UI, hub and Blender add-on |
| `rebuild` | All of the above, starting from `clean` |
| `clean` | Deletes `build/`, `dist/` and caches (keeps `.venv` and your data) |
| `run` / `hub` | Starts the app (http://127.0.0.1:8000) or the Progress Hub (http://127.0.0.1:8765) |
| `exe` | Builds the standalone app `dist\Decompress\Decompress.exe` (Python not needed) and smoke-tests it |
| `installer` | Builds `dist\DecompressSetup-<ver>.exe`, which adds Start Menu and Desktop shortcuts and an uninstaller (Windows; needs Inno Setup) |
| `shortcut` | Adds Desktop and Start Menu shortcuts for this checkout. They point to the exe if built, otherwise to the venv |
| `release` | `rebuild` + `exe` + `installer`: everything |

Change the optional extras with `-Extras all,dev` (Windows) or `EXTRAS=all,dev` (Linux). `all` adds YOLO.
On Windows, if PowerShell blocks the script, use `build.cmd` (it bypasses the policy for that run only), or run
`Set-ExecutionPolicy -Scope CurrentUser RemoteSigned` once.

### Desktop app, exe and shortcuts (Windows)

```bash
build exe
```

```bash
build shortcut
```

`Decompress.exe` (or the shortcut) starts the app and the Progress Hub, then opens your browser. Closing its
console window stops both. Your data is kept in `%USERPROFILE%\Decompress`. The exe also accepts any CLI
command, for example `Decompress.exe analyze clip.mp4`.

To make an installer you can copy to another PC, install Inno Setup once with `winget install JRSoftware.InnoSetup`, then run:

```bash
build installer
```

This writes `dist\DecompressSetup-0.1.0.exe`. It installs per user (no admin needed) and adds Start Menu and
Desktop shortcuts plus an uninstaller. CI builds the same installer on every push; download it from the
**DecompressSetup** artifact on the Actions run. The app folder is about 450 MB, mostly OpenCV and MediaPipe.

**Install the built package on another machine:**

```bash
pip install "decompress-0.1.0-py3-none-any.whl[mediapipe]"
```

That provides three commands:

| Command | Purpose |
|---|---|
| `decompress serve` | The app (also `discover`, `analyze`, `train`, `backends`, `hub`, `blender-addon`) |
| `decompress-hub` | The Progress Hub dashboard |
| `decompress-hub-client` | Report progress to the hub (`sync`, `task`, `log`, `show`) |

MediaPipe downloads its pose model (~9 MB) on first use. On headless Linux it also needs `libegl1` and `libgles2`.
CI (`.github/workflows/ci.yml`) runs `rebuild` on Windows and Linux with Python 3.11 and 3.12 on every push, and
uploads `dist/` as a build artifact.

Examples:

```bash
decompress discover --subnet 192.168.1.0/24
```

```bash
decompress --backend mediapipe analyze my_clip.mp4      # writes to decompress_data/outputs/
```

## Blender

**As an add-on (recommended):** in Blender, go to Edit → Preferences → Add-ons → Install from Disk, and choose
`dist/decompress_blender_addon.zip`. Then use File → Import → **Decompress Motion (.motion.json)**. It offers
options for the track ID (0 imports everyone), the smoothing window, and baking to keyframes.

**From a terminal:**

```bash
blender --python decompress/blender/import_motion.py -- decompress_data/outputs/<job>-clip.motion.json --bake
```

Options: `--track N` imports one person, `--smooth 5` sets the smoothing window (frames), `--bake` bakes the motion to keyframes.
Use the MediaPipe backend for true 3D. With 2D-only backends the motion is placed on the camera plane until 2D→3D lifting (P6.4) lands.

## Connecting cameras

In **Discover cameras**, scan your network or enter a camera by hand, type its username and password, and press **Connect**.
For ONVIF cameras, Decompress asks the camera for its stream address (highest-resolution profile) and finds out whether
it supports pan/tilt/zoom, so you don't need to know any URL. With **remember** ticked, the camera is saved and comes
back automatically next time the app starts.

Passwords are kept in Windows Credential Manager (or the macOS Keychain / Linux Secret Service). If no OS store is
available, they go to an encrypted file in your data folder. They are never written into `cameras.json` or shown in URLs.
On a tile, **⤢** focuses that view, double-clicking the picture goes fullscreen, and PTZ cameras get an arrow pad
(hold to move, release to stop).

## Training your own movements

1. Get recordings: click **● Record motion** on a live tile, or drop videos into the queue.
2. Open **Train movements**, pick a recording and a person (track), and scrub with the slider or the arrow keys
   (Shift+arrow jumps 10 frames, Space plays). Mark the movement with **[ Set start** / **Set end ]** (or the `[` and `]`
   keys), type a name such as `waving`, and press **Add clip**.
3. Add clips of at least two movements (two or more clips each gives you an accuracy estimate), then press **Train**.
   Every live tile starts labelling people with your movements straight away, and the readout under the player
   shows what the classifier thinks at the current frame.

Clips are stored in `models/labels.json` in your data folder. `decompress train <labels.json>` trains from the command line too.

## Claude Progress Hub

```bash
decompress-hub                                          # dashboard at http://127.0.0.1:8765
```

```bash
decompress-hub-client sync plan.json                    # push this project's plan
```

```bash
decompress-hub-client task decompress P1.4 in_progress --note "GetStreamUri"
```

```bash
decompress-hub-client show                              # progress bars in the terminal
```

After changing `plan.json`, run `python scripts/render_plan.py` to refresh PLAN.md. Hub data is stored in
`~/.claude-progress-hub/hub.json` (override with `HUB_DATA`). See [hub/README.md](hub/README.md) for connecting
other Claude projects.

## Responsible use

Only scan networks and view cameras you own or are authorised to access. The scanner refuses public
address ranges and large subnets, never tries default or guessed passwords, and binds the web UI to
localhost by default.

## Tests

Run `build test` (Windows) or `./scripts/build.sh test`. The `verify` step also smoke-tests the installed wheel.
