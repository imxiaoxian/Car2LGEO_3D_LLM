"""Local Studio catalogue; binary layout verified against Studio's file reader.

The scene uses LDraw coordinates. Studio serializes connection rotations in
column order; its grid has a ten LDU pitch and a reversed local Z direction.
Unsupported record types fail closed instead of inventing connection evidence.
"""

import hashlib
import struct
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import numpy as np




KINDS = {0: "axle", 1: "ball", 2: "hole", 3: "stud", 4: "fixed", 6: "hinge", 7: "rail", 8: "slider",
         16: "axle", 128: "ball", 1024: "hole", 8192: "stud", 32768: "fixed", 262144: "hinge", 2097152: "rail", 16777216: "slider"}
AXLE_TYPES = {2: "pin_socket", 3: "pin", 5: "pin_solid", 6: "axle_socket", 7: "axle", 9: "axle_guard",
              10: "round_hole", 11: "round_bar", 12: "clip", 13: "bar", 14: "small_pin_socket", 15: "bar_socket", 17: "small_pin"}
AXLE_TYPES.update(dict(zip((64, 256, 1024, 4096, 16384, 65536, 262144, 1048576, 4194304,
                            16777216, 67108864, 268435456, 1073741824), AXLE_TYPES.values())))


@dataclass(frozen=True)
class Port:
    id: str
    kind: str
    subtype: int
    position: tuple
    direction: tuple
    altitude: int = 0
    area: int = 0
    length: float = 0
    semantic: str = ""
    start_capped: bool = False
    end_capped: bool = False
    grabbing_required: bool = False
    tag: str = ""
    grabbing: bool = False
    cross_type: bool = False
    forward: tuple = (0., 0., 1.)


class Reader:
    def __init__(self, data):
        self.data, self.offset = data, 0

    def read(self, fmt):
        size = struct.calcsize("<" + fmt)
        if self.offset + size > len(self.data):
            raise ValueError("Truncated Studio connectivity record")
        values = struct.unpack_from("<" + fmt, self.data, self.offset)
        self.offset += size
        return values[0] if len(values) == 1 else values

    def string(self):
        size, shift = 0, 0
        for _ in range(5):
            byte = self.read("B")
            size |= (byte & 127) << shift
            if not byte & 128:
                break
            shift += 7
        else:
            raise ValueError("Invalid .NET string length")
        if self.offset + size > len(self.data):
            raise ValueError("Truncated string")
        result = self.data[self.offset:self.offset + size].decode("utf-8")
        self.offset += size
        return result


@lru_cache(maxsize=8192)
def parse_connections(data: bytes) -> tuple[Port, ...]:
    r, version = Reader(data), 0
    if data.startswith(b"!!HS"):
        r.offset = 4
        version, file_type = r.read("ii")
        if version != 1:
            raise ValueError(f"Unsupported connectivity version {version}")
        if file_type != 0:
            raise ValueError(f"Not a connectivity file: {file_type}")
        end = data.find(b"!!DS", r.offset)
        if end < 0:
            raise ValueError("Missing connectivity data header")
        r.offset = end + 4
    integer = "i" if version else "h"
    ports, record = [], 0
    while r.offset < len(data):
        kind_id, subtype = r.read(integer), r.read(integer)
        kind = KINDS.get(kind_id)
        if kind is None:
            raise ValueError(f"Unsupported connection kind {kind_id} at {r.offset}")
        matrix = np.asarray(r.read("9f")).reshape(3, 3).T
        origin = np.asarray(r.read("3f"))
        if not np.isfinite(matrix).all() or not np.isfinite(origin).all():
            raise ValueError("Nonfinite connector")
        direction = matrix @ [0, 1, 0]
        prefix = f"{record}"
        if kind in ("stud", "hole"):
            rows, columns = r.read("hh")
            if rows < 0 or columns < 0 or rows > 1000 or columns > 1000:
                raise ValueError("Invalid connectivity grid")
            if rows and columns:
                for i in range(rows + 1):
                    for j in range(columns + 1):
                        altitude, area = r.read(integer), r.read(integer)
                        if altitude == -1 or not area:
                            continue
                        position = origin + matrix @ [j * 10, 0, -i * 10]
                        ports.append(Port(f"{prefix}:{i}:{j}", kind, subtype, tuple(position), tuple(direction), altitude, area))
        else:
            length, start, end_cap, required, tag = 0., False, False, False, ""
            if kind == "axle":
                start, end_cap, length, grabbing, required = r.read("??f??")
            elif kind == "fixed":
                r.read("h")
                if version and r.read("?"):
                    tag = r.string()
            elif kind == "hinge":
                r.read("4f?")
                if version and r.read("?"):
                    tag = r.string()
            elif kind == "rail":
                length = r.read("f")
            elif kind == "slider":
                start, end_cap, length, cylindrical = r.read("??f?")
            elif kind == "ball" and version and r.read("?"):
                count = r.read("h")
                if not 0 <= count <= 1000:
                    raise ValueError("Invalid ball attribute count")
                for _ in range(count):
                    r.read("f")
            semantic = AXLE_TYPES.get(subtype, "unknown") if kind == "axle" else kind
            if length < 0 or not np.isfinite(length):
                raise ValueError("Invalid connector length")
            ports.append(Port(prefix, kind, subtype, tuple(origin), tuple(direction), length=length,
                              semantic=semantic, start_capped=start, end_capped=end_cap, grabbing_required=required, tag=tag,
                              grabbing=semantic == "clip", cross_type=semantic in ("axle", "axle_guard", "axle_socket"),
                              forward=tuple(matrix @ [0., 0., 1.])))
        if version and r.read("?"):
            r.string()
        record += 1
    return tuple(ports)
