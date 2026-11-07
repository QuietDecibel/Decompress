import numpy as np

from decompress.vision import MovementClassifier, Tracker, describe_movement
from decompress.vision.movement import joint_angles, pose_features
from helpers import det, skeleton


def test_tracker_keeps_ids_and_separates_people():
    tr = Tracker()
    ids = []
    for i in range(10):
        tracks = tr.update([det(skeleton(cx=100 + 5 * i)), det(skeleton(cx=500 - 5 * i))], t=i / 10)
        ids.append(sorted(t.id for t in tracks))
    assert all(x == [1, 2] for x in ids)
    assert all(t.confirmed for t in tr.tracks)


def test_tracker_drops_lost_tracks():
    tr = Tracker(max_misses=2)
    tr.update([det(skeleton())], 0)
    for i in range(4):
        tr.update([], i + 1)
    assert tr.tracks == []


def test_describe_walking_and_arms():
    tr = Tracker()
    for i in range(15):   # ~0.75 body heights per second
        tr.update([det(skeleton(cx=100 + 8 * i, arms_up=True))], t=i / 10)
    label = describe_movement(tr.tracks[0].history)
    assert "walking" in label and "both arms raised" in label

    tr = Tracker()
    for i in range(15):
        tr.update([det(skeleton(knee_bend=0.35))], t=i / 10)
    assert describe_movement(tr.tracks[0].history).startswith("crouching")


def test_joint_angles_straight_arm():
    assert joint_angles(skeleton())["left_elbow"] > 170


def test_classifier_learns_two_movements(tmp_path):
    rng = np.random.default_rng(0)

    def wave(n=30):
        seq = []
        for i in range(n):
            kp = skeleton(arms_up=True)
            kp[9, 0] += 30 * np.sin(i / 2)       # left wrist swings
            kp[:, :2] += rng.normal(0, 2, (17, 2))
            seq.append(kp)
        return seq

    def squat(n=30):
        return [skeleton(knee_bend=0.3 * abs(np.sin(i / 5))) + np.r_[rng.normal(0, 2, (17, 2)).T, np.zeros((1, 17))].T
                for i in range(n)]

    clf = MovementClassifier(k=3).fit([wave(), wave(), squat(), squat()], ["wave", "wave", "squat", "squat"])
    assert clf.predict(wave())[0] == "wave"
    assert clf.predict(squat())[0] == "squat"
    clf.save(tmp_path / "m.json")
    assert MovementClassifier.load(tmp_path / "m.json").predict(wave())[0] == "wave"
    assert pose_features(wave()).shape == (63,)
