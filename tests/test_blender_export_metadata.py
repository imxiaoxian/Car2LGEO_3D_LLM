import ast
import json
from types import SimpleNamespace

import pytest

from car2lego3d.common import ROOT


@pytest.fixture
def property_json():
    source = ast.parse((ROOT / "scripts/blender_export_render.py").read_text(encoding="utf-8"))
    helpers = ast.Module(body=[node for node in source.body if isinstance(node, ast.FunctionDef)
        and node.name in {"metadata_json", "property_json"}], type_ignores=[])
    namespace = {"json": json}
    exec(compile(helpers, "blender_export_render.py", "exec"), namespace)
    return namespace["property_json"]


class IDGroup:
    def __init__(self, **values):
        self.values = values

    def to_dict(self):
        return self.values


class IDArray:
    def __init__(self, *values):
        self.values = values

    def to_list(self):
        return list(self.values)


def test_nested_blender_ports_and_joint_vectors_roundtrip_as_json(property_json):
    obj = dict(car_ports=[IDGroup(id="axle", position_m=IDArray(0, .2, .3),
        interface=IDGroup(axis=IDArray(0, 1, 0), mates=[IDGroup(id="hub")]))],
        car_joint=IDGroup(type="revolute", origin_m=IDArray(0, .2, .3),
            axis=IDArray(0, 1, 0), limits=(-3.14, 3.14)))
    exported = dict(ports=property_json(obj, "car_ports", []), joint=property_json(obj, "car_joint", None))
    restored = json.loads(json.dumps(exported))
    assert restored["ports"] == [dict(id="axle", position_m=[0, .2, .3],
        interface=dict(axis=[0, 1, 0], mates=[dict(id="hub")]))]
    assert restored["joint"]["axis"] == [0, 1, 0]
    assert restored["joint"]["limits"] == [-3.14, 3.14]


def test_string_metadata_and_missing_values_remain_compatible(property_json):
    assert property_json({"car_ports": '[{"id":"glass-seat","position_m":[1,2,3]}]'},
        "car_ports", []) == [dict(id="glass-seat", position_m=[1, 2, 3])]
    assert property_json({}, "car_ports", []) == []
    assert property_json({}, "car_joint", None) is None
    with pytest.raises(TypeError, match="Unsupported scene metadata type"):
        property_json({"car_ports": [object()]}, "car_ports", [])


def test_inspection_rig_keeps_shaders_and_exposes_interior_without_softbox_glare():
    source = ast.parse((ROOT / "scripts/blender_export_render.py").read_text(encoding="utf-8"))
    helper = next(node for node in source.body if isinstance(node, ast.FunctionDef)
                  and node.name == "configure_inspection_lighting")
    import numpy as np
    objects, created = [], []
    background = SimpleNamespace(inputs={key: SimpleNamespace(default_value=None) for key in ("Color", "Strength")})
    scene = SimpleNamespace(objects=objects, world=SimpleNamespace(use_nodes=False,
        node_tree=SimpleNamespace(nodes={"Background": background})))

    class Direction(np.ndarray):
        def to_track_quat(self, *args):
            return SimpleNamespace(to_euler=lambda: (0, 0, 0))

    context = SimpleNamespace(object=None)
    def add(type, location):
        light = SimpleNamespace(type="LIGHT", location=np.array(location).view(Direction),
            visible_glossy=True, data=SimpleNamespace())
        objects.append(light)
        created.append(light)
        context.object = light
    bpy = SimpleNamespace(context=context, ops=SimpleNamespace(object=SimpleNamespace(light_add=add)),
        data=SimpleNamespace(objects=SimpleNamespace(remove=lambda obj, do_unlink: objects.remove(obj))))
    namespace = dict(bpy=bpy, Vector=lambda value: np.array(value).view(Direction))
    exec(compile(ast.Module(body=[helper], type_ignores=[]), "lighting", "exec"), namespace)
    result = namespace["configure_inspection_lighting"](scene, np.zeros(3).view(Direction))
    assert background.inputs["Strength"].default_value == .04
    assert len(created) == 3 and all(not light.visible_glossy for light in created)
    assert [light.data.energy for light in created] == [2200, 1800, 2400]
    assert result["material_shaders_modified"] is False and result["optical_measurement"] is False
