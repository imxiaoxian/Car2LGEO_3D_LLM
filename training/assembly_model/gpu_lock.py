"""Compatibility import for the shared website/Blender/training GPU lock."""
from pathlib import Path
import sys

REPOSITORY = Path(__file__).resolve().parents[2]
SOURCE = str(REPOSITORY / "src")
if SOURCE not in sys.path:
    sys.path.insert(0, SOURCE)

from car2lego3d.gpu_lock import DEFAULT_LOCK, GPULock

__all__ = ["DEFAULT_LOCK", "GPULock"]
