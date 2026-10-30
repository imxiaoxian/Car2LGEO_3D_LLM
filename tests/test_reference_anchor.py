"""Reference identity must survive ingestion without changing PNG bytes."""
import sys

from PIL import Image, PngImagePlugin
import pytest

from car2lego3d import gpt_reference


def test_png_identity_and_own_anchor_survive_worker_failure(tmp_path, monkeypatch):
    source = tmp_path / "provider"
    service = source / "backend/app/services/reference_images.py"
    service.parent.mkdir(parents=True)
    service.write_text("# Test fixture\n", encoding="utf-8")
    output = tmp_path / "images"
    output.mkdir()
    anchor = output / "identity-anchor.png"
    metadata = PngImagePlugin.PngInfo()
    metadata.add_text("source", "Original vehicle crop")
    Image.new("RGB", (32, 32), "white").save(anchor, pnginfo=metadata)
    original = anchor.read_bytes()
    provided = tmp_path / "source.png"
    provided.write_bytes(original)
    monkeypatch.setattr(gpt_reference, "configuration", lambda: (source, sys.executable))
    calls = []

    class FailedWorker:
        def __init__(self, *args, **kwargs):
            calls.append(anchor.read_bytes())

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def wait(self):
            return 1

    monkeypatch.setattr(gpt_reference.subprocess, "Popen", FailedWorker)
    for path in (provided, anchor):
        with pytest.raises(RuntimeError, match="reference generation failed"):
            gpt_reference.generate("Countach", output, reference_image=path)
    assert calls == [original, original]
