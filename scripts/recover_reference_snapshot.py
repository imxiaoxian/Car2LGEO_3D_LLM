"""Restore immutable reference pixels into a new, unverified artifact revision."""
import argparse
from copy import deepcopy
import json
from pathlib import Path
import shutil

from car2lego3d.common import digest, write_json
from car2lego3d.references import load_references


def recover(snapshot, previous, output):
    snapshot, previous, output = (Path(p).resolve() for p in (snapshot, previous, output))
    if output.exists():
        raise ValueError('Recovery requires a new directory; preserve all prior artifacts')
    if output.is_relative_to(previous) or output.is_relative_to(snapshot.parent):
        raise ValueError('Recovery must be separate from the source evidence')
    manifest, images = load_references(snapshot)
    upstream_path = previous / 'car2lego/references/manifest.json'
    upstream = json.loads(upstream_path.read_text(encoding='utf-8'))
    if upstream.get('brief') != manifest.get('brief'):
        raise ValueError('Source cache and snapshot identify different vehicle briefs')
    records = deepcopy(manifest['shots'])
    for name, row in records.items():
        relative = Path(row['path'])
        if relative.is_absolute() or '..' in relative.parts or row['view'] != name:
            raise ValueError('Unsafe or mismatched source reference path')
        if not row.get('model', '').startswith('gpt-image-'):
            raise ValueError('Recovery requires actual GPT image provenance')
        row['consistency'] = dict(status='unverified', issues=[])
    anchor = records['overview']
    if manifest.get('identity_lock', {}).get('anchor_sha256') != anchor['sha256']:
        raise ValueError('Identity observations do not belong to the saved anchor')
    output.mkdir(parents=True)
    for name, path in images.items():
        destinations = (output / records[name]['file'], output / 'car2lego' / records[name]['path'])
        for destination in destinations:
            if not destination.resolve().is_relative_to(output):
                raise ValueError('Reference destination escaped recovery directory')
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(path, destination)
            if digest(destination) != records[name]['sha256']:
                raise ValueError('Recovered image differs from immutable source')
    shutil.copyfile(images['overview'], output / 'reference.png')
    # Keep exact generation cache keys; never inherit a review or stage completion.
    upstream.update(images=list(records.values()), brief=manifest['brief'],
        identity_lock=manifest['identity_lock'], expected_shots=manifest['expected_shots'],
        shot_requests={name: row['shot_request'] for name, row in records.items()})
    for key in ('content_review', 'reference_gate', 'repair_tasks'):
        upstream.pop(key, None)
    write_json(output / 'car2lego/references/manifest.json', upstream)
    restored = {key: value for key, value in manifest.items()
                if key not in {'content_review', 'reference_gate', 'repair_tasks'}}
    restored.update(shots=records, views={name: records[name] for name in manifest['views']},
        cross_view_consistency_verified=False, appearance_accepted=False)
    write_json(output / 'views.json', restored)
    write_json(output / 'reference-corrections.json',
        {name: row['correction'] for name, row in records.items() if row.get('correction')})
    for cache in ('identity-lock', 'batch-review'):
        if (previous / cache).is_dir():
            shutil.copytree(previous / cache, output / cache)
    report = dict(schema='reference-recovery.v1', snapshot=str(snapshot),
        snapshot_sha256=digest(snapshot), previous=str(previous),
        previous_manifest_sha256=digest(upstream_path), restored_images=len(images),
        images={name: row['sha256'] for name, row in records.items()},
        anchor_sha256=anchor['sha256'], stage_completed=False,
        reference_admitted=False, appearance_accepted=False)
    write_json(output / 'recovery.json', report)
    load_references(output / 'views.json')
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('snapshot', 'previous', 'output'):
        parser.add_argument('--' + name, type=Path, required=True)
    result = recover(**vars(parser.parse_args()))
    print(json.dumps({key: result[key] for key in ('restored_images', 'anchor_sha256', 'reference_admitted')}))
