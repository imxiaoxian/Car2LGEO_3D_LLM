import importlib.util
import json

from PIL import Image
import pytest

from car2lego3d.common import ROOT, digest, write_json
from car2lego3d.references import load_references


spec = importlib.util.spec_from_file_location('recover_reference_snapshot', ROOT / 'scripts/recover_reference_snapshot.py')
recovery = importlib.util.module_from_spec(spec)
spec.loader.exec_module(recovery)


def sources(tmp_path):
    snapshot = tmp_path / 'snapshot/views.json'
    previous = tmp_path / 'old'
    records = {}
    for index, name in enumerate(('overview', 'front', 'back', 'left', 'right')):
        path = snapshot.parent / 'shots' / (name + '.png')
        path.parent.mkdir(parents=True, exist_ok=True)
        Image.new('RGB', (8, 8), (index * 40, 10, 20)).save(path)
        records[name] = dict(view=name, id=name, file='shots/' + path.name,
            path='references/' + path.name, sha256=digest(path), model='gpt-image-test',
            shot_request=dict(view=name, dependency_fingerprint=name),
            correction='original ' + name, consistency=dict(status='pass'))
    manifest = dict(schema_version=2, shots=records,
        views={name: records[name] for name in ('front', 'back', 'left', 'right')},
        expected_shots=[dict(id=name) for name in records], brief=dict(model='Countach'),
        identity_lock=dict(anchor_sha256=records['overview']['sha256']),
        content_review=dict(status='pass'), reference_gate=dict(allowed=True), repair_tasks=[],
        cross_view_consistency_verified=True)
    write_json(snapshot, manifest)
    write_json(previous / 'car2lego/references/manifest.json',
        dict(fingerprint='original-cache-key', brief=manifest['brief'], images=[]))
    return snapshot, previous


def test_recovery_preserves_pixels_and_cache_without_granting_admission(tmp_path):
    snapshot, previous = sources(tmp_path)
    output = tmp_path / 'new'
    original_hash = digest(snapshot)
    report = recovery.recover(snapshot, previous, output)
    restored, images = load_references(output / 'views.json')
    assert len(images) == report['restored_images'] == 5
    assert digest(snapshot) == original_hash
    assert not report['reference_admitted'] and not report['stage_completed']
    assert not restored['cross_view_consistency_verified']
    assert 'content_review' not in restored and 'reference_gate' not in restored
    assert all(row['consistency']['status'] == 'unverified' for row in restored['shots'].values())
    upstream = json.loads((output / 'car2lego/references/manifest.json').read_text())
    assert upstream['fingerprint'] == 'original-cache-key'
    assert digest(output / 'car2lego/references/overview.png') == digest(images['overview'])
    assert json.loads((output / 'reference-corrections.json').read_text())['overview'] == 'original overview'
    assert not (output / 'image.json').exists()
    with pytest.raises(ValueError, match='new directory'):
        recovery.recover(snapshot, previous, output)


def test_recovery_rejects_changed_source_before_creating_revision(tmp_path):
    snapshot, previous = sources(tmp_path)
    Image.new('RGB', (8, 8), 'red').save(snapshot.parent / 'shots/front.png')
    with pytest.raises(ValueError, match='checksum'):
        recovery.recover(snapshot, previous, tmp_path / 'new')
    assert not (tmp_path / 'new').exists()


def test_recovery_rejects_unrelated_cache(tmp_path):
    snapshot, previous = sources(tmp_path)
    write_json(previous / 'car2lego/references/manifest.json', dict(brief=dict(model='Other')))
    with pytest.raises(ValueError, match='different vehicle briefs'):
        recovery.recover(snapshot, previous, tmp_path / 'new')
    assert not (tmp_path / 'new').exists()
