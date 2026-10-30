"""Check approved dataset partitions without creating or approving any samples."""
from hashlib import sha256
from itertools import combinations
import json


def partition_identity(rows):
    result = {key: set() for key in ("families", "modules", "models", "trajectories", "inputs")}
    for row in rows:
        source = row["source"]
        result["families"].update(source.get("families") or [source["family"]])
        result["families"].add(source["family"])
        result["modules"].update(source["module_hashes"])
        result["models"].add(source["model_id"])
        result["trajectories"].add(source["trajectory_sha256"])
        result["inputs"].add(sha256(row["messages"][0]["content"].encode()).hexdigest())
    return result


def audit_splits(partitions, *, expected_snapshot, expected_protocol=None):
    """Call only after content-bound execution evidence has been validated."""
    identities = {}
    summary = {}
    protocol_hashes = set()
    for name, rows in partitions.items():
        if not rows:
            raise ValueError(f"Dataset partition {name} has no approved samples")
        for row in rows:
            source = row["source"]
            if source["snapshot"] != expected_snapshot:
                raise ValueError(f"Dataset partition {name}: corpus snapshot mismatch")
            protocol_hashes.add(source["protocol_hash"])
            if expected_protocol and source["protocol_hash"] != expected_protocol:
                raise ValueError(f"Dataset partition {name}: protocol mismatch")
        identity = partition_identity(rows)
        identities[name] = identity
        summary[name] = dict(samples=len(rows), **{key: len(value) for key, value in identity.items()})
    if len(protocol_hashes) != 1:
        raise ValueError("Dataset partitions must use one identical frozen tool protocol")
    overlaps = []
    for left, right in combinations(identities, 2):
        for key in identities[left]:
            shared = identities[left][key] & identities[right][key]
            if shared:
                overlaps.append(dict(left=left, right=right, kind=key, count=len(shared)))
    if overlaps:
        raise ValueError("Dataset split leakage: " + json.dumps(overlaps, sort_keys=True))
    held_out = sorted(name for name in partitions if name != "train")
    return dict(schema="assembly-dataset-split-audit.v1",
        status="disjoint" if held_out else "held_out_not_provided",
        partitions=summary, protocol_hash=next(iter(protocol_hashes)),
        snapshot=expected_snapshot, held_out_partitions=held_out, overlaps=[])
