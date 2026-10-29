import json
import pytest
from unittest.mock import patch

from car2lego3d import reference_review
from car2lego3d.reference_review import validate_review
from car2lego3d.common import digest, write_json


def record(name, status='pass', issues=None, correction=''):
    issues = issues or []
    return dict(id=name, status=status, observed_state='closed', issues=issues, correction=correction,
        defects=[dict(issue_index=i, kind='other', modules=['body'], expected=correction or issue,
            verification='Inspect the requested target view') for i, issue in enumerate(issues)])


def test_review_cannot_pass_with_observed_errors_or_omit_targets():
    with pytest.raises(ValueError, match='omitted or duplicated'):
        validate_review(dict(shots=[record('front')]), ['front', 'left'])
    with pytest.raises(ValueError, match='Incomplete'):
        validate_review(dict(shots=[record('left', issues=['door is open'])]), ['left'])
    with pytest.raises(ValueError, match='Incomplete'):
        validate_review(dict(shots=[record('left', status='needs_revision')]), ['left'])
    assert validate_review(dict(shots=[record('left', status='needs_revision',
        issues=['door is open'], correction='Close both doors')]), ['left'])[0]['status'] == 'needs_revision'


@pytest.mark.parametrize('records', [[None], [dict(id=['left'])]])
def test_malformed_target_records_are_schema_errors(records):
    with pytest.raises(ValueError, match='omitted or duplicated'):
        validate_review(dict(shots=records), ['left'])


def test_exact_attachment_label_alias_preserves_findings_and_raw_response():
    from copy import deepcopy
    response = dict(shots=[record('TARGET_left', status='needs_revision',
        issues=['door is open'], correction='Close door'), record('right')])
    original = deepcopy(response)
    result = validate_review(response, ['left', 'right'])
    assert result[0] == dict(original['shots'][0], id='left')
    assert result[0]['status'] == 'needs_revision' and result[0]['issues'] == ['door is open']
    assert response == original


@pytest.mark.parametrize('ids', [
    ['TARGET_left', 'left'], ['TARGET_left', 'TARGET_rear'],
    ['target_left', 'right'], ['TARGET_TARGET_left', 'right'],
])
def test_attachment_alias_cannot_hide_duplicates_unknown_views_or_fuzzy_ids(ids):
    with pytest.raises(ValueError, match='omitted or duplicated'):
        validate_review(dict(shots=[record(name) for name in ids]), ['left', 'right'])


def test_invalid_review_gets_bounded_schema_repair_without_losing_visible_errors(tmp_path):
    invalid = dict(shots=[record('left', issues=['door is open'])])
    corrected = dict(shots=[record('left', status='needs_revision', issues=['door is open'], correction='Close door')])
    with patch.object(reference_review, 'ask_json', side_effect=[invalid, corrected]) as ask:
        result = reference_review.ask_validated_review([dict(role='user', content='Inspect images')],
            tmp_path, 'review', lambda response: validate_review(response, ['left']))
    assert result == corrected['shots'] and result[0]['status'] == 'needs_revision'
    assert ask.call_count == 2
    assert ask.call_args.args[2] == 'review-schema-repair-1'
    assert 'door is open' in ask.call_args.args[0][-2]['content']
    saved = json.loads((tmp_path / 'review-schema-failure-1.json').read_text())
    assert saved['response'] == invalid


def test_repeated_invalid_reviews_stop_without_fabricating_a_pass(tmp_path):
    with patch.object(reference_review, 'ask_json', return_value=dict(shots=[])) as ask, \
            pytest.raises(ValueError, match='omitted or duplicated'):
        reference_review.ask_validated_review([], tmp_path, 'review', lambda response: validate_review(response, ['left']))
    assert ask.call_count == 3
    assert len(list(tmp_path.glob('review-schema-failure-*.json'))) == 3


def test_missing_classification_repairs_review_before_deciding_to_regenerate(tmp_path):
    from copy import deepcopy
    incomplete = record('front', status='needs_revision', issues=['Six rim holes'], correction='Use five holes')
    del incomplete['defects']
    corrected = deepcopy(incomplete)
    corrected['defects'] = [dict(issue_index=0, kind='rim_geometry', modules=['wheels'],
        expected='five holes', verification='count resolved wheel openings')]
    with patch.object(reference_review, 'ask_json', side_effect=[dict(shots=[incomplete]), dict(shots=[corrected])]) as ask:
        rows = reference_review.ask_validated_review([], tmp_path, 'review', lambda r: validate_review(r, ['front']))
    assert ask.call_count == 2 and rows[0]['issues'] == incomplete['issues']
    gate = reference_review.assess_reference_gate(dict(front=dict(rows[0], sha256='image-hash')))
    assert gate['allowed'] and gate['repair_tasks'][0]['kind'] == 'rim_geometry'
    with pytest.raises(ValueError, match='structured defects'):
        validate_review(dict(shots=[{k: v for k, v in record('front').items() if k != 'defects'}]), ['front'])


def test_invalid_json_reply_is_repaired_without_restarting_image_generation(tmp_path):
    text = '{"shots":[{"id":"front" "status":"needs_revision"}]}'
    try:
        json.loads(text)
    except json.JSONDecodeError as invalid:
        error = reference_review.ModelJSONError(text, invalid)
    corrected = dict(shots=[record('front', status='needs_revision', issues=['Wrong camera'], correction='Use front view')])
    with patch.object(reference_review, 'ask_json', side_effect=[error, corrected]) as ask:
        result = reference_review.ask_validated_review([], tmp_path, 'review', lambda r: validate_review(r, ['front']))
    assert result[0]['status'] == 'needs_revision' and ask.call_count == 2
    assert ask.call_args.args[0][-2]['content'] == text
    failure = json.loads((tmp_path / 'review-schema-failure-1.json').read_text())
    assert failure['raw_text'] == text and failure['error_kind'] == 'invalid_model_json'


def test_repeated_invalid_json_stops_at_the_existing_review_budget(tmp_path):
    try:
        json.loads('{')
    except json.JSONDecodeError as invalid:
        error = reference_review.ModelJSONError('{', invalid)
    with patch.object(reference_review, 'ask_json', side_effect=error) as ask, \
            pytest.raises(reference_review.ModelJSONError):
        reference_review.ask_validated_review([], tmp_path, 'review', lambda r: validate_review(r, ['front']))
    assert ask.call_count == 3


def test_review_batches_follow_the_camera_plan_not_request_completion_order(tmp_path):
    names = ['overview', 'front', 'back', 'left', 'right', 'top', 'front_right']
    shots = {name: dict(sha256=name, camera=name) for name in reversed(names)}
    manifest = dict(shots=shots, expected_shots=[dict(id=name) for name in names])
    write_json(tmp_path / 'views.json', manifest)
    images = {name: tmp_path / (name + '.png') for name in reversed(names)}
    batches = []

    def ask(messages, *args, **kwargs):
        batch = list(json.loads(messages[1]['content'][0]['text'])['targets'])
        batches.append(batch)
        return dict(shots=[record(name) for name in batch])

    with patch.object(reference_review, 'load_references', return_value=(manifest, images)), \
            patch.object(reference_review, 'image_content', return_value=[]), \
            patch.object(reference_review, 'ask_json', side_effect=ask), \
            patch.object(reference_review, 'digest', return_value='manifest-hash'):
        report = reference_review.review_references(tmp_path / 'views.json', tmp_path / 'review')
    assert batches == [names[:5], names[5:]]
    assert report['status'] == 'pass'


def test_review_snapshot_cannot_be_overwritten_by_a_different_manifest(tmp_path):
    path = tmp_path / 'views.json'
    write_json(path, dict(shots={}))
    folder = tmp_path / 'review'
    folder.mkdir()
    previous = folder / 'reference-manifest.json'
    previous.write_text('{"old": true}', encoding='utf-8')
    expected = digest(previous)
    with patch.object(reference_review, 'load_references', return_value=({}, {})), \
            pytest.raises(ValueError, match='different references'):
        reference_review.review_references(path, folder)
    assert digest(previous) == expected


def test_final_review_reuses_exact_batch_evidence_but_rechecks_changed_inputs(tmp_path):
    from PIL import Image
    images, shots = {}, {}
    for name in ('overview', 'front', 'left'):
        images[name] = tmp_path / (name + '.png')
        Image.new('RGB', (20, 20), 'white').save(images[name])
        shots[name] = dict(file=name + '.png', sha256=digest(images[name]), camera=name,
            state='closed', focus='body', modules=['body'])
    manifest = dict(brief={}, identity_lock=None, shots=shots,
        expected_shots=[dict(id=name) for name in images])
    path = tmp_path / 'views.json'
    write_json(path, manifest)
    calls = []

    def ask(messages, *args, **options):
        names = list(json.loads(messages[1]['content'][0]['text'])['targets'])
        calls.append(names)
        return dict(shots=[record(name) for name in names])

    with patch.object(reference_review, 'ask_json', side_effect=ask):
        reference_review.review_camera_batch(images['overview'], {},
            {name: dict(shots[name], file=str(images[name])) for name in ('front', 'left')},
            tmp_path / 'batch-review')
        with patch.object(reference_review, 'load_references', return_value=(manifest, images)):
            report = reference_review.review_references(path, tmp_path / 'final')
            assert calls == [['front', 'left'], ['overview']]
            assert set(report['shots']) == set(images)
            source, = report['reused_batch_reviews']
            assert digest(tmp_path / 'final' / source['file']) == source['sha256']
            manifest['shots']['left']['state'] = 'door open'
            write_json(path, manifest)
            second = reference_review.review_references(path, tmp_path / 'changed')
    assert calls[-1] == list(images) and not second['reused_batch_reviews']


def test_conflicting_exact_batch_verdicts_require_fresh_review(tmp_path):
    from PIL import Image
    image = tmp_path / 'overview.png'
    Image.new('RGB', (20, 20), 'white').save(image)
    shot = dict(file=str(image), camera='overview', state='closed', focus='identity', modules=['body'])
    with patch.object(reference_review, 'ask_json', return_value=dict(shots=[record('overview')])):
        report = reference_review.review_camera_batch(image, {}, dict(overview=shot), tmp_path / 'batch-review')
    conflicting = json.loads(json.dumps(report))
    conflicting['shots']['overview'] = dict(record('overview', 'needs_revision', ['Wrong body'], 'Restore body'),
        sha256=digest(image))
    write_json(tmp_path / 'batch-review/conflicting/report.json', conflicting)
    output = tmp_path / 'final'
    output.mkdir()
    rows, sources = reference_review.reusable_batch_reviews(
        dict(brief={}, identity_lock=None, shots=dict(overview=shot)), dict(overview=image), tmp_path, output, 'gpt-6.1-sol')
    assert not rows and len(sources) == 2


def test_batch_review_cache_is_bound_to_anchor_camera_identity_and_actual_target(tmp_path):
    from PIL import Image
    anchor, target = tmp_path / 'anchor.png', tmp_path / 'side.png'
    Image.new('RGB', (10, 10), 'white').save(anchor)
    Image.new('RGB', (10, 10), 'blue').save(target)
    targets = dict(left=dict(file=str(target), camera='left', state='closed', modules=['body']))
    called = []

    def ask(messages, directory, name, **options):
        called.append((directory, options))
        return dict(shots=[record('left')])

    with patch.object(reference_review, 'ask_json', side_effect=ask):
        first = reference_review.review_camera_batch(anchor, {}, targets, tmp_path / 'reviews', {'paint': 'white'})
        path = called[-1][0] / 'report.json'
        original = digest(path)
        reference_review.review_camera_batch(anchor, {}, targets, tmp_path / 'reviews', {'paint': 'white'})
        assert called[-1][0] == called[-2][0] and called[-1][1]['reuse_completed'] is True
        targets['left']['state'] = 'open'
        reference_review.review_camera_batch(anchor, {}, targets, tmp_path / 'reviews', {'paint': 'white'})
        Image.new('RGB', (10, 10), 'green').save(target)
        reference_review.review_camera_batch(anchor, {}, targets, tmp_path / 'reviews', {'paint': 'white'})
        reference_review.review_camera_batch(anchor, {}, targets, tmp_path / 'reviews', {'paint': 'red'})
    assert len({directory for directory, _ in called}) == 4
    assert digest(path) == original and first['shots']['left']['sha256'] != digest(target)
    assert first['appearance_accepted'] is False


def test_reference_retries_keep_previous_review_evidence(tmp_path):
    from car2lego3d import gpt_reference
    manifest = tmp_path / 'views.json'
    write_json(manifest, dict(shots={'overview': dict(sha256='image')}, views={}))
    write_json(tmp_path / 'image.json', dict(outputs={}))
    reports = []

    def review(path, directory):
        report = dict(status='pass', shots={'overview': dict(status='pass', sha256='image')})
        report_path = directory / 'report.json'
        write_json(report_path, report)
        reports.append((report_path, digest(report_path)))
        return report

    with patch.object(gpt_reference, '_generate_once', return_value=manifest), \
            patch.object(reference_review, 'review_references', side_effect=review):
        gpt_reference.generate('car', tmp_path)
        gpt_reference.generate('car', tmp_path)
    assert reports[0][0] != reports[1][0]
    assert all(digest(path) == sha for path, sha in reports)
    latest = json.loads(manifest.read_text())['content_review']
    assert (tmp_path / latest['file']).resolve() == reports[-1][0].resolve()


def test_final_reference_gate_keeps_raw_failure_and_creates_mandatory_repair(tmp_path):
    from car2lego3d import gpt_reference
    path = tmp_path / 'views.json'
    write_json(path, dict(shots={'overview': dict(sha256='image')}, views={}))
    write_json(tmp_path / 'image.json', dict(outputs={}))
    def review(manifest, directory):
        report = dict(status='needs_revision', shots=dict(overview=dict(status='needs_revision', sha256='image',
            issues=['Six holes'], defects=[dict(issue_index=0, kind='rim_geometry', modules=['wheels'],
                expected='five holes', verification='final rim geometry')], correction='Use five holes')))
        write_json(directory / 'report.json', report)
        return report
    with patch.object(gpt_reference, '_generate_once', return_value=path) as generate, \
            patch.object(reference_review, 'review_references', side_effect=review):
        assert gpt_reference.generate('car', tmp_path) == path
    saved = json.loads(path.read_text())
    assert generate.call_count == 1
    assert not saved['cross_view_consistency_verified']
    assert saved['content_review']['status'] == 'needs_revision'
    assert saved['reference_gate']['allowed'] and saved['repair_tasks'][0]['status'] == 'open'
    assert (tmp_path / 'reference-gate.json').is_file() and (tmp_path / 'repair-tasks.json').is_file()


def test_changed_camera_drops_only_its_old_corrections_and_preserves_history(tmp_path):
    from car2lego3d.gpt_reference import load_corrections
    from car2lego3d.reference_plan import Shot
    import json
    old = [Shot('overview', 'anchor', ()), Shot('seats_pedals', 'dashboard-facing', ('cabin',)),
           Shot('front', 'front', ('front',))]
    current = [old[0], Shot('seats_pedals', 'rearward seat view', ('cabin',)), old[2]]
    original = dict(seats_pedals='Show both seats and pedals', front='Lower front camera')
    path = tmp_path / 'reference-corrections.json'
    write_json(path, original)
    original_hash = digest(path)
    write_json(tmp_path / 'car2lego/references/manifest.json', dict(expected_shots=[s.record() for s in old]))
    assert load_corrections(tmp_path, current) == dict(front='Lower front camera')
    preserved = tmp_path / 'correction-history' / (original_hash + '.json')
    assert json.loads(preserved.read_text()) == original
    assert load_corrections(tmp_path, current) == dict(front='Lower front camera')
    assert digest(preserved) == original_hash


@pytest.mark.parametrize('confirmed', [False, True])
def test_blocking_findings_are_reinspected_individually_and_retained(tmp_path, confirmed):
    from PIL import Image
    anchor, target = tmp_path / 'anchor.png', tmp_path / 'front.png'
    Image.new('RGB', (32, 32), 'white').save(anchor)
    Image.new('RGB', (32, 32), 'blue').save(target)
    initial = record('front', status='needs_revision', issues=['This is the rear'], correction='Show the front')
    focused = initial if confirmed else record('front')
    response = dict(shots=[focused], reinspection=dict(
        disposition='confirmed' if confirmed else 'misattributed',
        explanation='Rear lamps are visible' if confirmed else 'The labelled target shows the nose and windscreen; rear lamps belong to the other target'))
    targets = dict(front=dict(file=str(target), camera='front', modules=['front']))
    with patch.object(reference_review, 'ask_json', return_value=response) as ask:
        result, evidence = reference_review.reinspect_blockers([initial], anchor, {}, targets, tmp_path, None, 'review-model')
    assert result['front']['status'] == ('needs_revision' if confirmed else 'pass')
    assert evidence['front']['initial']['issues'] == ['This is the rear']
    assert evidence['front']['source_sha256'] == digest(target)
    assert len(list(tmp_path.glob('focused-*-finding.json'))) == 1
    content = ask.call_args.args[0][1]['content']
    assert [row['text'] for row in content if row['type'] == 'text'][1:] == ['IDENTITY_ANCHOR', 'TARGET_front']
    assert list(json.loads(content[0]['text'])['targets']) == ['front']


def test_focused_pass_cannot_erase_a_blocker_without_explaining_misattribution(tmp_path):
    from PIL import Image
    image = tmp_path / 'target.png'
    Image.new('RGB', (32, 32), 'white').save(image)
    initial = record('front', status='needs_revision', issues=['Wrong camera'], correction='Show the front')
    with patch.object(reference_review, 'ask_json', return_value=dict(shots=[record('front')])) as ask, \
            pytest.raises(ValueError, match='misattribution evidence'):
        reference_review.reinspect_blockers([initial], image, {}, dict(front=dict(file=str(image))), tmp_path, None, 'model')
    assert ask.call_count == 3


def test_local_repair_debt_does_not_trigger_repeated_vision_review(tmp_path):
    from PIL import Image
    image = tmp_path / 'target.png'
    Image.new('RGB', (32, 32), 'white').save(image)
    initial = record('front', status='needs_revision', issues=['Six rim holes'], correction='Use five holes')
    initial['defects'] = [dict(issue_index=0, kind='rim_geometry', modules=['wheels'],
        expected='five holes', verification='count holes in fitted wheel geometry')]
    with patch.object(reference_review, 'ask_json') as ask:
        result, evidence = reference_review.reinspect_blockers([initial], image, {}, dict(front=dict(file=str(image))), tmp_path, None, 'model')
    assert not ask.called and not evidence
    assert reference_review.assess_reference_gate(result)['status'] == 'ready_with_repairs'


def test_pose_reinspection_uses_verified_closed_parent_and_retains_wrong_edge_finding(tmp_path):
    from PIL import Image
    images = {name: tmp_path / (name + '.png') for name in ('overview', 'door_left_closed', 'scissor_hinge')}
    for index, path in enumerate(images.values()):
        Image.new('RGB', (30, 20), (index * 70, 10, 20)).save(path)
    shots = dict(scissor_hinge=dict(parents={'door_left_closed': digest(images['door_left_closed'])}))
    targets = reference_review.comparison_targets(['scissor_hinge'], shots, images)
    initial = record('scissor_hinge', 'needs_revision', ['Door is upright'], 'Lower door')
    reply = dict(shots=[record('TARGET_scissor_hinge')], reinspection=dict(disposition='misinterpreted',
        explanation='The prior result used the steep short front edge; the long lower edge is shallow relative to the fixed sill and the closed door.'))
    with patch.object(reference_review, 'ask_json', return_value=reply) as ask:
        result, evidence = reference_review.reinspect_blockers([initial], images['overview'], {}, targets,
            tmp_path, None, 'test-model')
    assert result['scissor_hinge']['status'] == 'pass'
    assert evidence['scissor_hinge']['initial']['issues'] == ['Door is upright']
    request = ask.call_args.args[0]
    assert 'long front-to-rear lower edge' in request[0]['content']
    assert [r['text'] for r in request[1]['content'] if r['type'] == 'text'][1:] == [
        'IDENTITY_ANCHOR', 'TARGET_scissor_hinge', 'CONTEXT_door_left_closed']
    Image.new('RGB', (30, 20), 'red').save(images['door_left_closed'])
    with pytest.raises(ValueError, match='does not match'):
        reference_review.reinspect_blockers([initial], images['overview'], {}, targets, tmp_path, None, 'test-model')


def test_batch_review_cache_binds_closed_comparison_pixels(tmp_path):
    from PIL import Image
    images, shots = {}, {}
    for index, name in enumerate(('overview', 'door_left_closed', 'scissor_hinge')):
        images[name] = tmp_path / (name + '.png')
        Image.new('RGB', (20, 20), (index * 70, 0, 10)).save(images[name])
        shots[name] = dict(file=name + '.png', sha256=digest(images[name]), camera=name,
            state='closed' if name != 'scissor_hinge' else 'intermediate', focus='door', modules=['doors'])
    shots['scissor_hinge']['parents'] = dict(door_left_closed=digest(images['door_left_closed']))
    manifest = dict(shots=shots, brief={}, identity_lock=None)
    targets = reference_review.comparison_targets(['scissor_hinge'], shots, images)
    with patch.object(reference_review, 'ask_json', return_value=dict(shots=[record('scissor_hinge')])):
        reference_review.review_camera_batch(images['overview'], {}, targets, tmp_path / 'batch-review')
    output = tmp_path / 'final'
    output.mkdir()
    results, sources = reference_review.reusable_batch_reviews(manifest, images, tmp_path, output, 'gpt-6.1-sol')
    assert 'scissor_hinge' in results and len(sources) == 1
    Image.new('RGB', (20, 20), 'red').save(images['door_left_closed'])
    shots['scissor_hinge']['parents']['door_left_closed'] = digest(images['door_left_closed'])
    results, sources = reference_review.reusable_batch_reviews(manifest, images, tmp_path, output, 'gpt-6.1-sol')
    assert not results and not sources
