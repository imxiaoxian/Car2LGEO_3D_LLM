from copy import deepcopy
import json

import pytest

from car2lego3d.assembly_prompt_codec import (
    METADATA, PORT_FORMAT, content_hash, pack_payload, pack_ports, table_inventory,
    unpack_payload, unpack_ports, wire,
)


def native_ports():
    return [dict(id=str(i), kind="axle" if i % 3 else "hole", subtype=2,
        position=[i * 20, -0.0, -1e-8], direction=[0, 0, 1], altitude=0,
        area=0, length=20, semantic="pin_socket", start_capped=False,
        end_capped=False, grabbing_required=False, tag="", grabbing=False,
        cross_type=False, forward=[0, 1, 0]) for i in range(106)]


def payload():
    return dict(known_evidence=dict(parts={"2695.dat": dict(part="2695.dat",
        status="unverified", errors=["missing collider"], hashes=dict(connectors="native hash"),
        ports=native_ports())}), vehicle=dict(task="车门与玻璃"), state=dict(revision=8),
        recent_history=[dict(observation=dict(result=dict(ports=native_ports())))])


def test_all_port_fields_order_flags_and_evidence_roundtrip_without_mutation():
    original = payload()
    before = wire(original)
    packed = pack_payload(original)
    assert wire(original) == before
    decoded = unpack_payload(json.loads(wire(packed)))
    assert wire(decoded) == before
    assert len(wire(packed)) < len(wire(original)) / 2
    assert decoded["known_evidence"]["parts"]["2695.dat"]["status"] == "unverified"
    assert len(table_inventory(packed)) == 2
    assert table_inventory(packed)[0]["ports"] == 106
    packed["known_evidence"]["parts"]["2695.dat"]["ports"]["shared"]["direction"][0] = 1
    assert wire(original) == before


def test_new_fields_and_native_types_are_not_discarded_or_coerced():
    ports = native_ports()
    for index, port in enumerate(ports):
        port["unknown_connector_metadata"] = {"label": "custom", "value": 1 if index else True}
    packed = pack_ports(ports)
    assert packed["format"] == PORT_FORMAT
    assert "unknown_connector_metadata" in packed["columns"]
    restored = unpack_ports(packed)
    assert type(restored[0]["unknown_connector_metadata"]["value"]) is bool
    assert type(restored[1]["unknown_connector_metadata"]["value"]) is int
    assert wire(restored) == wire(ports)


@pytest.mark.parametrize("ports", [[], [{"id": "0"}], [{"id": "0"}, {"id": "1", "length": None}],
                                  [{"id": "0"}, {"id": "1"}], [None, {}]])
def test_empty_small_and_heterogeneous_records_are_preserved(ports):
    assert pack_ports(ports) == ports
    assert unpack_ports(pack_ports(ports)) == ports


@pytest.mark.parametrize("change", ["coordinate", "width", "count", "duplicate_column", "shared_column", "unknown"])
def test_corrupt_port_tables_cannot_be_decoded_as_exact_evidence(change):
    table = pack_ports(native_ports())
    if change == "coordinate":
        table["rows"][0][table["columns"].index("position")][0] = 0.125
    elif change == "width":
        table["rows"][0].append(9)
    elif change == "count":
        table["count"] += 1
    elif change == "duplicate_column":
        table["columns"].append(table["columns"][0])
    elif change == "shared_column":
        table["shared"][table["columns"][0]] = "conflict"
    else:
        table["unrecognized"] = 1
    with pytest.raises(ValueError):
        unpack_ports(table)


def test_non_port_changes_and_changed_encoding_cannot_pass_roundtrip_verification():
    packed = pack_payload(payload())
    changed = deepcopy(packed)
    changed["state"]["revision"] += 1
    with pytest.raises(ValueError, match="integrity mismatch"):
        unpack_payload(changed)
    changed = deepcopy(packed)
    changed[METADATA]["schema"] = "future"
    with pytest.raises(ValueError, match="Unsupported"):
        unpack_payload(changed)
    with pytest.raises(ValueError):
        pack_payload(packed)
    with pytest.raises(ValueError):
        unpack_payload(payload())


def test_nested_ids_keep_exact_reference_paths_and_port_order():
    original = {"known/evidence": {"~parts": {"ports": native_ports()}}}
    packed = pack_payload(original)
    row = table_inventory(packed)[0]
    assert row["pointer"] == "/known~1evidence/~0parts/ports"
    assert row["sha256"] == content_hash(native_ports())
    assert unpack_payload(packed) == original


def test_nonfinite_geometry_is_rejected_instead_of_serialized_as_json_nan():
    original = payload()
    original["known_evidence"]["parts"]["2695.dat"]["ports"][0]["position"][0] = float("nan")
    with pytest.raises(ValueError):
        pack_payload(original)
