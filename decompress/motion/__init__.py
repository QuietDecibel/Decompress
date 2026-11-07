"""Record tracked poses over time and export them for Blender (decompress-motion JSON v1).

Blender-side conversion lives in blender/import_motion.py so that script stays self-contained.
"""
from .recorder import MotionRecorder, load_motion

__all__ = ["MotionRecorder", "load_motion"]
