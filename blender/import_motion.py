"""Import a Decompress .motion.json into Blender as an animated armature.

Run from a terminal:
    blender --python blender/import_motion.py -- path/to/clip.motion.json [--track 3] [--smooth 5] [--bake]
or open it in Blender's Text Editor, set MOTION_PATH below, and press Run Script.

For each tracked person this creates:
  * one keyframed empty per joint (the raw motion capture), and
  * an armature whose bones follow those empties via Copy Location / Stretch To constraints.
With --bake the constraint motion is baked into plain bone keyframes (ready for retargeting / NLA).

The pure-Python helpers (no bpy) are importable for testing.
"""
from __future__ import annotations

import json
import sys

import numpy as np

MOTION_PATH = ""   # used when run from Blender's Text Editor

# Bones as (name, head_joint, tail_joint, parent). Derived joints: pelvis, neck.
RIG = [
    ("root",        "pelvis",          "neck",            None),
    ("head",        "neck",            "nose",            "root"),
    ("shoulder.L",  "neck",            "left_shoulder",   "root"),
    ("upper_arm.L", "left_shoulder",   "left_elbow",      "shoulder.L"),
    ("forearm.L",   "left_elbow",      "left_wrist",      "upper_arm.L"),
    ("shoulder.R",  "neck",            "right_shoulder",  "root"),
    ("upper_arm.R", "right_shoulder",  "right_elbow",     "shoulder.R"),
    ("forearm.R",   "right_elbow",     "right_wrist",     "upper_arm.R"),
    ("hip.L",       "pelvis",          "left_hip",        "root"),
    ("thigh.L",     "left_hip",        "left_knee",       "hip.L"),
    ("shin.L",      "left_knee",       "left_ankle",      "thigh.L"),
    ("hip.R",       "pelvis",          "right_hip",       "root"),
    ("thigh.R",     "right_hip",       "right_knee",      "hip.R"),
    ("shin.R",      "right_knee",      "right_ankle",     "thigh.R"),
]


# ---------------------------------------------------------------- pure helpers
def load_motion(path: str) -> dict:
    with open(path) as f:
        d = json.load(f)
    if d.get("format") != "decompress-motion":
        raise ValueError(f"{path} is not a decompress-motion file")
    return d


def smooth(points: np.ndarray, window: int = 5) -> np.ndarray:
    """Centred moving average over time. points: (T, J, C)."""
    if window <= 1 or len(points) < window:
        return points
    pad = window // 2
    padded = np.pad(points, ((pad, pad), (0, 0), (0, 0)), mode="edge")
    c = np.cumsum(padded, axis=0, dtype=np.float64)
    c = np.concatenate([np.zeros((1,) + c.shape[1:]), c])
    return (c[window:] - c[:-window]) / window


def to_blender_coords(track: dict, image_size, height_m: float = 1.7) -> np.ndarray:
    """(T, 17, 3) joint positions in Blender space (Z up, metres).

    Uses metric world landmarks when present (MediaPipe backend); otherwise maps 2D keypoints onto
    the camera plane (Y = 0), scaled so the subject is about height_m tall.
    """
    frames = track["frames"]
    if frames and all(f.get("world") for f in frames):
        w = np.array([f["world"] for f in frames], dtype=np.float64)   # x right, y down, z depth
        out = np.stack([w[..., 0], w[..., 2], -w[..., 1]], axis=-1)
        out[..., 2] -= out[..., 2].min()
        return out
    kp = np.array([f["kp2d"] for f in frames], dtype=np.float64)[..., :2]
    W, H = image_size
    heights = kp[:, :, 1].max(1) - kp[:, :, 1].min(1)
    px_per_m = (float(np.median(heights)) or H / 2) / (height_m * 0.95)
    x = (kp[..., 0] - W / 2) / px_per_m
    z = (kp[..., 1].max() - kp[..., 1]) / px_per_m
    return np.stack([x, np.zeros_like(x), z], axis=-1)


def with_derived(points: np.ndarray, joints: list[str]) -> tuple[np.ndarray, list[str]]:
    """Append pelvis and neck (mid-hips / mid-shoulders)."""
    j = {n: i for i, n in enumerate(joints)}
    pelvis = (points[:, j["left_hip"]] + points[:, j["right_hip"]]) / 2
    neck = (points[:, j["left_shoulder"]] + points[:, j["right_shoulder"]]) / 2
    return np.concatenate([points, pelvis[:, None], neck[:, None]], axis=1), joints + ["pelvis", "neck"]


def prepare(motion: dict, track_id: int | None = None, window: int = 5) -> list[dict]:
    """Return [{id, frames:[frame numbers], joints, points (T, J, 3)}] ready for Blender."""
    out = []
    for tr in motion["tracks"]:
        if track_id is not None and tr["id"] != track_id:
            continue
        pts = smooth(to_blender_coords(tr, motion["image_size"]), window)
        pts, names = with_derived(pts, motion["joints"])
        f0 = tr["frames"][0]["f"]
        out.append({"id": tr["id"], "label": tr.get("label", "person"),
                    "frames": [f["f"] - f0 + 1 for f in tr["frames"]], "joints": names, "points": pts})
    return out


# ---------------------------------------------------------------- Blender side
def build(motion: dict, track_id=None, window=5, bake=False):
    import bpy

    scene = bpy.context.scene
    scene.render.fps = int(round(motion.get("fps", 30)))
    created = []
    last_frame = 1
    for tr in prepare(motion, track_id, window):
        coll = bpy.data.collections.new(f"decompress_{tr['label']}_{tr['id']}")
        scene.collection.children.link(coll)
        empties = {}
        for ji, name in enumerate(tr["joints"]):
            e = bpy.data.objects.new(f"T{tr['id']}_{name}", None)
            e.empty_display_size = 0.03
            e.empty_display_type = "SPHERE"
            coll.objects.link(e)
            for fi, frame in enumerate(tr["frames"]):
                e.location = tuple(map(float, tr["points"][fi, ji]))
                e.keyframe_insert("location", frame=frame)
            empties[name] = e

        arm_data = bpy.data.armatures.new(f"Rig_T{tr['id']}")
        arm = bpy.data.objects.new(f"Rig_T{tr['id']}", arm_data)
        coll.objects.link(arm)
        bpy.context.view_layer.objects.active = arm
        bpy.ops.object.mode_set(mode="EDIT")
        jidx = {n: i for i, n in enumerate(tr["joints"])}
        rest = tr["points"][0]
        for name, head, tail, parent in RIG:
            b = arm_data.edit_bones.new(name)
            b.head, b.tail = tuple(map(float, rest[jidx[head]])), tuple(map(float, rest[jidx[tail]]))
            if (b.tail - b.head).length < 1e-4:
                b.tail = (b.head[0], b.head[1], b.head[2] + 0.05)
            if parent:
                b.parent = arm_data.edit_bones[parent]
        bpy.ops.object.mode_set(mode="POSE")
        for name, head, tail, _ in RIG:
            pb = arm.pose.bones[name]
            c = pb.constraints.new("COPY_LOCATION"); c.target = empties[head]
            c = pb.constraints.new("STRETCH_TO"); c.target = empties[tail]
        bpy.ops.object.mode_set(mode="OBJECT")
        last_frame = max(last_frame, tr["frames"][-1])
        scene.frame_start, scene.frame_end = 1, last_frame
        if bake:
            bpy.ops.object.select_all(action="DESELECT")
            arm.select_set(True)
            bpy.context.view_layer.objects.active = arm
            bpy.ops.object.mode_set(mode="POSE")
            bpy.ops.pose.select_all(action="SELECT")
            bpy.ops.nla.bake(frame_start=1, frame_end=tr["frames"][-1], only_selected=True,
                             visual_keying=True, clear_constraints=True, bake_types={"POSE"})
            bpy.ops.object.mode_set(mode="OBJECT")
        created.append(arm.name)
    return created


def _cli(argv):
    import argparse
    ap = argparse.ArgumentParser(prog="import_motion.py")
    ap.add_argument("motion", nargs="?", default=MOTION_PATH)
    ap.add_argument("--track", type=int)
    ap.add_argument("--smooth", type=int, default=5)
    ap.add_argument("--bake", action="store_true")
    a = ap.parse_args(argv)
    if not a.motion:
        raise SystemExit("usage: blender --python import_motion.py -- clip.motion.json")
    print("created:", build(load_motion(a.motion), a.track, a.smooth, a.bake))


if __name__ == "__main__":
    _cli(sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else ([MOTION_PATH] if MOTION_PATH else []))
