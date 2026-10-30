"""Reinspect blocked reference findings without changing the website job or source images."""
import argparse
import json
from pathlib import Path
import shutil

from car2lego3d.common import digest, write_json
from car2lego3d.reference_gate import assess_reference_gate
from car2lego3d.reference_review import reinspect_blockers
from car2lego3d.references import load_references


def run(references, report, output):
    output = Path(output).resolve()
    if output.exists() and any(output.iterdir()):
        raise ValueError('Use a new diagnostic directory; preserve prior findings')
    manifest, images = load_references(references)
    original = json.loads(Path(report).read_text(encoding='utf-8'))
    rows = list(original['shots'].values())
    if any(row.get('sha256') != digest(images[row['id']]) for row in rows):
        raise ValueError('Reference changed since the original finding; do not adjudicate different pixels')
    snapshot = output / 'input-references'
    snapshot.mkdir(parents=True)
    shutil.copyfile(references, snapshot / 'views.json')
    for name, path in images.items():
        destination = snapshot / manifest['shots'][name]['file']
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, destination)
    manifest, images = load_references(snapshot / 'views.json')
    shutil.copyfile(report, output / 'original-report.json')
    targets = {name: {**manifest['shots'][name], 'file': path} for name, path in images.items()}
    resolved, evidence = reinspect_blockers(rows, images['overview'], manifest.get('brief'), targets,
        output, manifest.get('identity_lock'), original['model'])
    result = dict(schema='reference-reinspection.v1', original_report_sha256=digest(output / 'original-report.json'),
        reference_manifest_sha256=digest(snapshot / 'views.json'), shots=resolved, reinspections=evidence,
        reference_snapshot='input-references/views.json',
        reference_gate=assess_reference_gate(resolved), website_job_changed=False,
        appearance_accepted=False)
    write_json(output / 'report.json', result)
    return dict(reference_gate=result['reference_gate'], reinspected=list(evidence))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--references', type=Path, required=True)
    parser.add_argument('--report', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    print(json.dumps(run(**vars(parser.parse_args()))))
