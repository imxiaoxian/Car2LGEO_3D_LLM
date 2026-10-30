"""Read archived inputs and measure lossless port encoding without model requests."""
import argparse
import json
from pathlib import Path

from car2lego3d.assembly_prompt_codec import (
    VERSION, content_hash, pack_payload, table_inventory, unpack_payload, wire,
)
from car2lego3d.common import digest, write_json


def measure(source, output):
    source_hash = digest(source)
    original = json.loads(source.read_text(encoding="utf-8"))
    packed = pack_payload(original)
    restored = unpack_payload(packed)
    if wire(restored) != wire(original):
        raise ValueError("Planner input did not round-trip exactly")
    write_json(output, packed)
    raw_bytes, packed_bytes = len(wire(original).encode()), len(wire(packed).encode())
    return dict(source=str(source.resolve()), source_sha256=source_hash,
        source_unchanged=source_hash == digest(source),
        payload_sha256=content_hash(original), packed=str(output.resolve()), packed_sha256=digest(output),
        original_wire_bytes=raw_bytes, packed_wire_bytes=packed_bytes,
        reduction_percent=round(100 * (raw_bytes-packed_bytes)/max(1, raw_bytes), 2),
        tables=table_inventory(packed), exact_roundtrip=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, action="append", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    sources = [source.resolve() for source in args.input]
    output = args.output.resolve()
    if output.exists():
        parser.error("Use a new output directory; archived planner evidence is immutable")
    if any(source == output or source.is_relative_to(output) for source in sources):
        parser.error("Output cannot contain source evidence")
    results = [measure(source, output / f"{index:04d}-packed.json")
               for index, source in enumerate(sources)]
    report = dict(schema="assembly-prompt-packing-probe.v1", codec=VERSION,
        model_requests=0, enabled_in_production=False, eligible_training_examples=0,
        quality="not_evaluated", decision_latency="not_measured", results=results,
        limits="Lossless byte comparison only. No planner or vehicle quality claim. "
               "A new frozen execution/context contract and same-input evaluation are needed before integration.")
    write_json(output / "report.json", report)
    print(json.dumps(report))


if __name__ == "__main__":
    main()
