"""Bind visible synthetic vehicle identity before generating dependent cameras."""
from hashlib import sha256
import json
from pathlib import Path
from PIL import Image

from .common import digest, write_json
from .dialogue import ask_json, image_content, settings
from .reference_review import ask_validated_review, validate_review
from .reference_gate import DEFECT_PROMPT, assess_reference_gate


FIELDS = ("paint", "interior", "wheels", "roof_glazing", "handedness",
          "door_window_grouping", "distinctive_geometry")
PROMPT = """Describe ONLY the visible identity of this synthetic vehicle anchor for subsequent
camera renders. Return one JSON object with these keys, each a concise string or null when not visible:
paint, interior, wheels, roof_glazing, handedness, door_window_grouping, distinctive_geometry.
Also return anchor_review: {id:'overview', status:'pass'|'needs_revision'|'indeterminate',
observed_state:string, issues:[string], correction:string}. Review the anchor against explicit visible
variant constraints in the brief. A contradictory anchor needs correction before generating other cameras.
issues contains visible defects only; pass requires issues:[]. Put occluded or uncertain identity details
in observed_state and use null for unseen identity fields. Do not demand invisible rear details in this view.
Record actual visible paint, upholstery colour, wheel opening/spoke count and rim-lip details,
opaque roof versus glazing, door/window boundaries, and optional wing or body trim.
Inspect the enlarged overlapping crops as well as the whole anchor. Count the LARGE rim openings
individually; do not confuse their count with the small hub bolts or repeat the expected count from text.
The supplied vehicle brief identifies the requested variant but cannot override what the image shows.
Do not infer hidden mechanisms, measurements, opposite-side asymmetries or unseen interior colours.
State uncertainty explicitly; do not substitute a familiar variant or a preferred colour.
This is a description of a generated image, not manufacturer-verified vehicle data.
The defects array belongs INSIDE anchor_review alongside issues, never among the identity fields.
""" + DEFECT_PROMPT


def validate_identity(observed):
    if not isinstance(observed, dict) or 'anchor_review' not in observed:
        raise ValueError("Anchor identity requires a visible variant review")
    observed = dict(observed)
    anchor_review = dict(observed.pop('anchor_review'))
    # Older responses placed the typed defects beside the identity observations.
    if 'defects' in observed:
        defects = observed.pop('defects')
        if 'defects' in anchor_review and anchor_review['defects'] != defects:
            raise ValueError("Conflicting nested and top-level anchor defects")
        anchor_review['defects'] = defects
    review = validate_review(dict(shots=[anchor_review]), ['overview'])[0]
    if (set(observed) != set(FIELDS)
            or any(value is not None and (not isinstance(value, str) or not value.strip())
                   for value in observed.values())):
        raise ValueError("Anchor identity requires visible descriptions or explicit null uncertainty")
    return observed, review


def lock_identity(anchor, brief, directory):
    anchor = Path(anchor)
    key = dict(anchor_sha256=digest(anchor), prompt_sha256=sha256(PROMPT.encode()).hexdigest(),
               model=settings()["model"], brief=brief)
    identity = sha256(json.dumps(key, sort_keys=True).encode()).hexdigest()
    output = Path(directory) / identity
    output.mkdir(parents=True, exist_ok=True)
    details = {}
    with Image.open(anchor) as image:
        width, height = image.size
        for side, bounds in (("LEFT", (0, .3, .65, 1)), ("RIGHT", (.35, .3, 1, 1))):
            crop = image.crop(tuple(round(value * (width if i % 2 == 0 else height))
                                    for i, value in enumerate(bounds)))
            crop.thumbnail((600, 600))
            path = output / (side.lower() + '-inspection.png')
            crop.resize((crop.width * 2, crop.height * 2)).save(path)
            details['ENLARGED_ANCHOR_' + side] = path
    observed, review = ask_validated_review([dict(role="system", content=PROMPT), dict(role="user", content=[
        dict(type="text", text=json.dumps(dict(vehicle_brief=brief))),
        *image_content({"IDENTITY_ANCHOR": anchor, **details})])], output, "identity", validate_identity, ask=ask_json,
        max_tokens=3000, timeout=180, reuse_completed=True, stream=True,
        reasoning_effort="medium", transient_retries=2)
    record = dict(schema="car2lego3d.reference-identity.v1", **key, observations=observed,
                  anchor_review=review, crops={name: dict(file=path.name, sha256=digest(path))
                                              for name, path in details.items()},
                  synthetic=True, physical_verified=False, appearance_accepted=False)
    record['reference_gate'] = assess_reference_gate({'overview': {**review, 'sha256': digest(anchor)}})
    write_json(output / "identity-lock.json", record)
    return record
