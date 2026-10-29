"""Process-safe cooperative lock shared by training, inference and Blender."""
from __future__ import annotations

import json
import os
from pathlib import Path
import time

from .common import ROOT

DEFAULT_LOCK = ROOT / "training/assembly_model/runtime/gpu.lock"


class GPULock:
    def __init__(self, path=None, *, purpose="training", timeout=600, poll=0.25):
        self.path = Path(path or os.getenv("ASSEMBLY_GPU_LOCK_PATH", DEFAULT_LOCK))
        self.purpose, self.timeout, self.poll = purpose, timeout, poll
        self.stream = None

    def __enter__(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        descriptor = os.open(self.path, os.O_RDWR | os.O_CREAT, 0o600)
        self.stream = os.fdopen(descriptor, "r+b")
        self.stream.seek(0, os.SEEK_END)
        if self.stream.tell() == 0:
            self.stream.write(b"\0")
            self.stream.flush()
        deadline = time.monotonic() + self.timeout
        while True:
            try:
                self.stream.seek(0)
                if os.name == "nt":
                    import msvcrt
                    msvcrt.locking(self.stream.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(self.stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError:
                if time.monotonic() >= deadline:
                    self.stream.close()
                    self.stream = None
                    raise TimeoutError(f"GPU busy; cooperative lock unavailable: {self.path}")
                time.sleep(self.poll)
        owner = dict(pid=os.getpid(), purpose=self.purpose, acquired_at=time.time())
        self.stream.seek(1)
        self.stream.write(json.dumps(owner).encode())
        self.stream.truncate()
        self.stream.flush()
        return self

    def __exit__(self, *_):
        if self.stream is None:
            return
        self.stream.seek(0)
        if os.name == "nt":
            import msvcrt
            msvcrt.locking(self.stream.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl
            fcntl.flock(self.stream.fileno(), fcntl.LOCK_UN)
        self.stream.close()
        self.stream = None
