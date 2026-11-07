"""Computer vision: person/creature detection, pose estimation, tracking, movement analysis."""
from .schema import Detection, JOINTS, BONES
from .tracker import Tracker, Track
from .backends import get_backend, available_backends
from .movement import describe_movement, pose_features, MovementClassifier

__all__ = ["Detection", "JOINTS", "BONES", "Tracker", "Track", "get_backend", "available_backends",
           "describe_movement", "pose_features", "MovementClassifier"]
