# Decompress — Project Plan

Source of truth: [`plan.json`](plan.json). Push it to the Progress Hub with
`decompress-hub-client sync plan.json`, and regenerate this file with `python scripts/render_plan.py`.
Overall: **29/43 done (67%)**. Legend: `[x]` done · `[~]` in progress · `[ ]` to do.

## P0 — Foundation & Progress Hub  (4/5)

- [x] **P0.1** Project plan & task breakdown (plan.json / PLAN.md)
- [x] **P0.2** Progress Hub server + REST API
- [x] **P0.3** Hub dashboard with live progress bars
- [x] **P0.4** Hub client so other Claude projects can report
- [ ] **P0.5** Host the hub where cloud Claude sessions can reach it — _needs a public URL + HUB_TOKEN_

## P1 — Camera discovery  (5/6)

- [x] **P1.1** ONVIF WS-Discovery probe — _unit-tested; validate on hardware (P1.6)_
- [x] **P1.2** Private-subnet port probe + RTSP OPTIONS check
- [x] **P1.3** Vendor stream-URL templates
- [x] **P1.4** ONVIF GetStreamUri with the user's credentials — _WS-Security digest auth; tested against a simulated ONVIF camera_
- [x] **P1.5** Encrypted credential store for cameras — _OS keyring (Windows Credential Manager etc.), else Fernet-encrypted file_
- [ ] **P1.6** Validate on real cameras (Hikvision, Dahua, Reolink, Wyze...)

## P2 — Video sources & multi-view  (4/5)

- [x] **P2.1** Capture threads with reconnect (RTSP / HTTP / webcam / file)
- [x] **P2.2** Multi-view grid UI (1x1 to 4x4)
- [x] **P2.3** Import videos by drag & drop
- [x] **P2.4** Focus/fullscreen tile + PTZ controls — _focus/fullscreen tiles, ONVIF PTZ pad; saved cameras auto-start_
- [ ] **P2.5** Hardware-accelerated decoding

## P3 — Video queue  (2/3)

- [x] **P3.1** Persistent queue with reorder / cancel / resume
- [x] **P3.2** Background worker writing annotated video + motion
- [ ] **P3.3** Parallel workers + import whole folders

## P4 — Detection, pose & tracking  (3/6)

- [x] **P4.1** Pluggable backends: MediaPipe, YOLO-pose, HOG
- [x] **P4.2** Multi-person tracker with stable IDs + trails
- [x] **P4.3** Rule-based movement description — _walking / running / crouching / arms raised_
- [~] **P4.4** Creature detection (YOLO animal classes) — _backend written; ultralytics not yet tested_
- [ ] **P4.5** Animal pose estimation (quadruped skeletons)
- [ ] **P4.6** Re-identify the same person across cameras

## P5 — Training on what it sees  (3/5)

- [x] **P5.1** Trainable movement classifier (k-NN on pose features)
- [x] **P5.2** Train from labelled clips (CLI)
- [x] **P5.3** Labelling UI in the app — _scrub, mark in/out, label, train, live prediction readout_
- [ ] **P5.4** Deep sequence model (temporal CNN / transformer)
- [ ] **P5.5** Fine-tune detectors on custom footage

## P6 — Blender motion rebuild  (3/6)

- [x] **P6.1** Record motion to .motion.json
- [x] **P6.2** Blender importer: armature + baked animation — _also installs as a Blender add-on (File > Import); verified with bpy 5.2_
- [x] **P6.3** Temporal smoothing
- [ ] **P6.4** 2D-to-3D lifting for 2D-only backends
- [ ] **P6.5** Retarget to Rigify / Mixamo rigs; BVH / FBX export
- [ ] **P6.6** Live link: stream poses into Blender in real time

## P7 — Quality & release  (5/7)

- [x] **P7.1** Automated test suite — _23 unit tests + wheel smoke test_
- [x] **P7.2** Docs (README, PLAN)
- [x] **P7.3** Install package: wheel, build/test/rebuild scripts (Windows + Linux), Blender add-on — _wheel smoke-tested in a clean venv; add-on tested in bpy 5.2_
- [ ] **P7.4** Web UI auth + HTTPS
- [x] **P7.5** CI: build + test on Windows and Linux — _Windows + Linux, Python 3.11/3.12 all green_
- [x] **P7.6** Standalone Windows app: Decompress.exe + installer with shortcuts — _built and install-tested on Windows CI (silent install, shortcuts, smoke test)_
- [ ] **P7.7** Docker image
