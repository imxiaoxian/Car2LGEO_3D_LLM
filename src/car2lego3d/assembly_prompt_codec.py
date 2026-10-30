"""Versioned, lossless port tables for vehicle planner inputs."""
from copy import deepcopy
from hashlib import sha256
import json


VERSION = "assembly-prompt-ports.v1"
PORT_FORMAT = "native-ports.columns.v1"
METADATA = "planner_encoding"
GUIDE = (
    "A ports table contains shared fields and rows in column order. Each exact port is "
    "{**shared, **dict(zip(columns, row))}. IDs, native coordinates, flags and order "
    "are unchanged. Use the original IDs in tool arguments. Raw records remain "
    "available through context resources. This is evidence encoding, not a different connector protocol."
)


def wire(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), allow_nan=False)


def content_hash(value):
    return sha256(wire(value).encode("utf-8")).hexdigest()


def pack_ports(ports):
    """Leave small or heterogeneous records verbatim; never infer missing fields."""
    if (not isinstance(ports, list) or len(ports) < 2
            or not all(isinstance(port, dict) for port in ports)):
        return deepcopy(ports)
    keys = set(ports[0])
    if any(set(port) != keys for port in ports):
        return deepcopy(ports)
    shared = {key: deepcopy(ports[0][key]) for key in sorted(keys)
              if all(wire(port[key]) == wire(ports[0][key]) for port in ports[1:])}
    preferred = ("id", "kind", "semantic", "position", "direction")
    columns = [key for key in preferred if key in keys and key not in shared]
    columns += sorted(keys - set(shared) - set(columns))
    table = dict(format=PORT_FORMAT, shared=shared, columns=columns,
                 rows=[[deepcopy(port[key]) for key in columns] for port in ports],
                 count=len(ports), sha256=content_hash(ports))
    return table if len(wire(table)) < len(wire(ports)) else deepcopy(ports)


def unpack_ports(table):
    if not isinstance(table, dict) or table.get("format") != PORT_FORMAT:
        return deepcopy(table)
    expected = {"format", "shared", "columns", "rows", "count", "sha256"}
    if set(table) != expected:
        raise ValueError("Invalid native port table fields")
    shared, columns, rows = table["shared"], table["columns"], table["rows"]
    if (not isinstance(shared, dict) or not isinstance(columns, list)
            or not all(isinstance(key, str) for key in columns)
            or len(set(columns)) != len(columns) or set(columns).intersection(shared)
            or not isinstance(rows, list) or type(table["count"]) is not int
            or len(rows) != table["count"]
            or any(not isinstance(row, list) or len(row) != len(columns) for row in rows)):
        raise ValueError("Invalid native port table shape")
    ports = [{**deepcopy(shared), **dict(zip(columns, deepcopy(row)))} for row in rows]
    if content_hash(ports) != table["sha256"]:
        raise ValueError("Native port table integrity mismatch")
    return ports


def _walk(value, *, decode=False):
    if isinstance(value, list):
        return [_walk(item, decode=decode) for item in value]
    if not isinstance(value, dict):
        return deepcopy(value)
    return {key: (unpack_ports(item) if decode else pack_ports(item))
            if key == "ports" else _walk(item, decode=decode) for key, item in value.items()}


def pack_payload(payload):
    """Produce a self-describing input without mutating source evidence."""
    if not isinstance(payload, dict) or METADATA in payload:
        raise ValueError("Expected a raw planner payload without encoding metadata")
    packed = _walk(payload)
    packed[METADATA] = dict(schema=VERSION, original_sha256=content_hash(payload), guide=GUIDE)
    return packed


def unpack_payload(packed):
    if not isinstance(packed, dict) or METADATA not in packed:
        raise ValueError("Missing planner encoding metadata")
    metadata = packed[METADATA]
    if (not isinstance(metadata, dict) or metadata.get("schema") != VERSION
            or set(metadata) != {"schema", "original_sha256", "guide"}):
        raise ValueError("Unsupported planner encoding")
    restored = _walk({key: value for key, value in packed.items() if key != METADATA}, decode=True)
    if content_hash(restored) != metadata["original_sha256"]:
        raise ValueError("Planner payload integrity mismatch")
    return restored


def table_inventory(value, pointer=""):
    """RFC6901 locations and exact port counts for experiment reports."""
    if isinstance(value, dict):
        if value.get("format") == PORT_FORMAT:
            return [dict(pointer=pointer, ports=value["count"], sha256=value["sha256"])]
        result = []
        for key, item in value.items():
            escaped = key.replace("~", "~0").replace("/", "~1")
            result.extend(table_inventory(item, pointer + "/" + escaped))
        return result
    if isinstance(value, list):
        return [table for index, item in enumerate(value)
                for table in table_inventory(item, pointer + "/" + str(index))]
    return []
