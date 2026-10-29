"""Inspect articulation landmarks on immutable pixels without changing admission."""
import argparse
import json
from pathlib import Path
import shutil

from car2lego3d.common import digest, write_json
from car2lego3d.dialogue import ask_json, image_content


def run(target, closed, output):
    output = Path(output).resolve()
    if output.exists():
        raise ValueError('Use a new diagnosis directory')
    output.mkdir(parents=True)
    images = {}
    for name, source in dict(TARGET=target, CLOSED_SAME_SIDE=closed).items():
        destination = output / (name.lower() + '.png')
        shutil.copyfile(source, destination)
        images[name] = destination
    result = ask_json([dict(role='system', content=
        'Inspect the two labelled synthetic Countach door images independently. Return JSON with '
        'observations, landmarks, projected_pose, ambiguity, defects. Compare the SAME door in its '
        'closed and open poses. Locate the visible long lower edge from the front lower corner to '
        'the rear lower corner of the moving DOOR, and the stationary sill. Report their endpoints '
        'as normalized image x,y if identifiable, otherwise null. Distinguish the long lower edge '
        'from the short front edge beside the hinge and from sloped window pillars. Explain whether '
        'the visible longitudinal door edge indicates intermediate or almost fully raised travel. '
        'Do not infer a precise 3D hinge angle from a single perspective photograph. State what '
        'cannot be determined. Identify visible ownership or shape defects separately. This is '
        'diagnosis only; no admission or pass verdict is requested.'), dict(role='user', content=[
            dict(type='text', text='Target inspection pose is approximately 35 degrees of scissor '
                'opening from closed. Diagnose whether the pixels and camera can establish that '
                'pose, using concrete landmarks rather than the familiar upright scissor-door silhouette.'),
            *image_content(images, label_images=True)])], output, 'diagnosis', max_tokens=4500,
        model='gpt-6.1-sol', timeout=180, stream=True, reuse_completed=True,
        reasoning_effort='medium', transient_retries=2)
    record = dict(schema='reference-pose-diagnosis.v1',
        inputs={name: dict(file=path.name, sha256=digest(path)) for name, path in images.items()},
        diagnosis=result, reference_admitted=False, physical_verified=False, appearance_accepted=False)
    write_json(output / 'report.json', record)
    return record


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('target', 'closed', 'output'):
        parser.add_argument('--' + name, type=Path, required=True)
    print(json.dumps(run(**vars(parser.parse_args())), ensure_ascii=False))
