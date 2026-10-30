"""Resolve native LDraw colours for the browser and exported materials."""
import csv
import math
from pathlib import Path
import re


def palette(root):
    result = {}
    path = Path(root) / "LDConfig.ldr"
    lines = path.read_text(encoding="utf-8-sig").splitlines() if path.is_file() else []
    for line in lines:
        tokens = line.split()
        if tokens[:2] != ["0", "!COLOUR"]:
            continue
        try:
            code = int(tokens[tokens.index("CODE") + 1])
            value = tokens[tokens.index("VALUE") + 1]
            if not re.fullmatch(r"#[a-fA-F0-9]{6}", value):
                continue
            alpha = int(tokens[tokens.index("ALPHA") + 1]) if "ALPHA" in tokens else 255
            result[code] = [int(value[offset:offset + 2], 16) for offset in (1, 3, 5)] + [alpha]
        except (ValueError, IndexError):
            continue
    # IO models can use Studio's extended LDraw codes absent from LDConfig.
    definition = Path(root).parent / "data/StudioColorDefinition.txt"
    if definition.is_file():
        with definition.open(encoding="utf-8-sig", newline="") as stream:
            for row in csv.DictReader(stream, delimiter="\t"):
                try:
                    code = int(row["LDraw Color Code"])
                    value = row["RGB value"]
                    alpha = float(row["Alpha"])
                    if (code < 0 or not re.fullmatch(r"#[a-fA-F0-9]{6}", value)
                            or not math.isfinite(alpha) or not 0 <= alpha <= 1):
                        continue
                    result.setdefault(code, [int(value[offset:offset + 2], 16)
                                             for offset in (1, 3, 5)] + [round(alpha * 255)])
                except (KeyError, ValueError, TypeError):
                    continue
    return result


def rgba(code, colors):
    if 0x2000000 <= code < 0x4000000:
        return [((code & 0xffffff) >> shift) & 255 for shift in (16, 8, 0)] + [128 if code >> 24 == 3 else 255]
    return colors.get(code)


def linear_color_factors(tree):
    for material in tree.get("materials", []):
        factor = material.get("pbrMetallicRoughness", {}).get("baseColorFactor")
        if factor:
            factor[:3] = [value / 12.92 if value <= .04045 else ((value + .055) / 1.055) ** 2.4
                          for value in factor[:3]]
