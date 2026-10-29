import sys

from car2lego3d import cli
from car2lego3d.mesh_envelope import carfit


def test_explicit_command_routes_to_migrated_converter(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(carfit, "convert_car", lambda **args: calls.append(args))
    monkeypatch.setattr(sys, "argv", ["car2lego3d", "mesh-envelope", "--mesh", "shape.glb",
        "--output", str(tmp_path), "--length-studs", "40", "--width-studs", "12"])
    cli.main()
    assert calls[0]["length_studs"] == 40 and calls[0]["width_studs"] == 12
    assert "scale_mode" not in calls[0]
