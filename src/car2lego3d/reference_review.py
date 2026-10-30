"""Vision review of actual generated references; manifests alone cannot verify content."""
import json
from hashlib import sha256
from pathlib import Path
import shutil

from .common import digest, write_json
from .dialogue import ask_json, image_content, ModelJSONError
from .references import load_references
from .reference_gate import DEFECT_PROMPT, assess_reference_gate, validate_defects


PROMPT = """Inspect the actual labelled vehicle reference images. The anchor locks identity, not pose.
Every image has an identifier printed above its pixels. Match findings to that identifier;
never assign observations from an adjacent target to this target. The label band is review metadata.
Check each TARGET against its own camera, state and focus contract. Check doors, covers, pop-up lamps,
wheel orientation, door/window ownership, light shape, body variant, rims and paint. Whole-car closed
views must NOT show raised scissor doors or raised pop-up headlamps. Open detail views must show the
requested moving assembly and surrounding attachment. Do not mistake a correct open-detail pose for
an identity inconsistency. Synthetic hidden mechanisms are design inference, never verified real data.
Return JSON {shots:[{id, status:'pass'|'needs_revision'|'indeterminate', observed_state, issues:[string],
correction:string, defects:[{issue_index:int, kind:string, modules:[string], expected:string,
verification:string}]}]}. Include each TARGET exactly once, including defects even when it is [].
Corrections must be specific to visible errors.
Use indeterminate for details that cannot be inspected. Never pass a view that violates its state.
The issues list contains observed defects only; pass requires issues:[]. Describe normal occlusion
and evidence limits in observed_state. For needs_revision or indeterminate supply a specific correction.
Judge visible content and useful framing, not whether a photograph proves measured thickness or hidden
mechanical accuracy. Do not demand that a single photo expose occluded surfaces. Hidden mechanisms can
remain uncertain if the requested attachment region is shown. Ignore sub-degree camera-elevation
differences; fail camera direction, wrong side, major perspective distortion or missing subject context.
""" + DEFECT_PROMPT

REINSPECTION_PROMPT = """This is a focused reinspection of ONE blocked target, with its identity anchor.
The prior multi-target finding is supplied as evidence, not as a required verdict. Inspect the image
whose embedded label matches TARGET. Explicitly address the prior finding using visible landmarks.
Do not change a failing verdict merely to unblock modelling. Keep uncertain evidence indeterminate.
In addition to shots, return reinspection:{disposition:'confirmed'|'misattributed'|'misinterpreted'|'unresolved',
explanation:string}. A pass requires misattributed or misinterpreted and a concrete explanation of the previous error.
Any remaining actual visible defects must still be listed and classified.
"""

COMPARISON_PROMPT = """CONTEXT images show the SAME door closed, for comparison only; do not return
reviews for CONTEXT images. Identify the moving door's long front-to-rear lower edge and the fixed
sill. Do not mistake its short front edge or sloping window pillar for the longitudinal opening
direction. Explain pose findings with those visible landmarks and the closed reference. Perspective
is not an exact 3D hinge-angle measurement. The requested approximate intermediate pose must still
be visually plausible; genuine fully closed/fully raised poses or ownership errors remain failures.
If the evidence cannot resolve the requested state, keep it indeterminate. Hidden mechanism limits
remain inferred and need actual 3D motion checks.
"""


def comparison_context(targets):
    images, hashes = {}, {}
    for name, row in targets.items():
        for parent, source in row.get('comparison_images', {}).items():
            path = Path(source['file'])
            if (not parent.endswith('_closed') or row.get('parents', {}).get(parent) != source['sha256']
                    or digest(path) != source['sha256']):
                raise ValueError('Closed-state comparison does not match the target parent')
            label = 'CONTEXT_' + parent
            if label in images and images[label] != path:
                raise ValueError('Conflicting closed-state comparison images')
            images[label] = path
            hashes.setdefault(name, {})[parent] = source['sha256']
    return images, hashes


def comparison_targets(names, shots, images):
    return {name: {**shots[name], 'file': images[name], 'comparison_images': {
        parent: dict(file=images[parent], sha256=sha) for parent, sha in shots[name].get('parents', {}).items()
        if parent.endswith('_closed')}} for name in names}


def reinspect_blockers(rows, anchor, brief, targets, directory, identity, model):
    initial = {row['id']: {**row, 'sha256': digest(Path(targets[row['id']]['file']))} for row in rows}
    resolved, evidence = dict(initial), {}
    for name in assess_reference_gate(initial)['blocked_views']:
        prior = initial[name]
        comparisons, _ = comparison_context({name: targets[name]})
        contract = {key: targets[name].get(key) for key in ('camera', 'state', 'focus', 'modules')}
        content = [dict(type='text', text=json.dumps(dict(brief=brief, identity_lock=identity,
            targets={name: contract}, prior_finding=prior))),
            *image_content({'IDENTITY_ANCHOR': Path(anchor)}, label_images=True),
            *image_content({'TARGET_' + name: Path(targets[name]['file'])}, label_images=True),
            *image_content(comparisons, label_images=True)]

        def validate(response):
            result = validate_review(response, [name])[0]
            review = response.get('reinspection', {})
            if (not isinstance(review, dict) or review.get('disposition') not in
                    {'confirmed', 'misattributed', 'misinterpreted', 'unresolved'}
                    or not isinstance(review.get('explanation'), str) or not review['explanation'].strip()
                    or (result['status'] == 'pass' and review['disposition'] not in {'misattributed', 'misinterpreted'})):
                raise ValueError('Focused reinspection must explain the prior finding; pass requires misattribution evidence')
            return result, review

        request_name = 'focused-' + sha256(name.encode()).hexdigest()[:12]
        result, review = ask_validated_review([
            dict(role='system', content=PROMPT + '\n' + REINSPECTION_PROMPT + ('\n' + COMPARISON_PROMPT if comparisons else '')),
            dict(role='user', content=content)], directory, request_name, validate,
            max_tokens=4000, model=model, stream=True, timeout=180, transient_retries=2,
            reuse_completed=True, reasoning_effort='medium')
        resolved[name] = {**result, 'sha256': prior['sha256']}
        evidence[name] = dict(initial=prior, result=resolved[name], reinspection=review,
            request_name=request_name, source_sha256=prior['sha256'])
        write_json(Path(directory) / (request_name + '-finding.json'), evidence[name])
    return resolved, evidence


def validate_review(response, names):
    if not isinstance(response, dict) or not isinstance(response.get('shots'), list):
        raise ValueError('Reference review requires a shots list')
    raw = response['shots']
    if (len(raw) != len(names)
            or any(not isinstance(r, dict) or not isinstance(r.get('id'), str) for r in raw)):
        raise ValueError('Reference review omitted or duplicated target shots')
    expected = set(names)
    # TARGET_ is our exact attachment label, not an alternate vehicle/view name.
    records = [dict(row, id=row['id'][7:] if row['id'] not in expected
                    and row['id'].startswith('TARGET_') and row['id'][7:] in expected else row['id'])
               for row in raw]
    if {r['id'] for r in records} != expected:
        raise ValueError('Reference review omitted or duplicated target shots: expected ' +
            repr(names) + ', received ' + repr([r['id'] for r in raw]))
    for item in records:
        if (item.get('status') not in {'pass', 'needs_revision', 'indeterminate'}
                or not isinstance(item.get('observed_state'), str)
                or not isinstance(item.get('issues'), list)
                or any(not isinstance(issue, str) for issue in item['issues'])
                or not isinstance(item.get('correction'), str)
                or (item['status'] != 'pass' and not item['correction'].strip())
                or (item['status'] == 'pass' and item['issues'])):
            raise ValueError('Incomplete reference content review')
        if 'defects' not in item:
            raise ValueError('Reference review requires structured defects for every issue; include defects:[] when there are no visible defects')
        validate_defects(item)
    return records


def ask_validated_review(messages, directory, name, validator, *, ask=None, **options):
    ask = ask or ask_json
    messages = list(messages)
    for attempt in range(3):
        try:
            response = ask(messages, directory, name if attempt == 0 else f"{name}-schema-repair-{attempt}", **options)
        except ModelJSONError as error:
            write_json(Path(directory) / f"{name}-schema-failure-{attempt + 1}.json",
                dict(error=str(error), error_kind='invalid_model_json', raw_text=error.text, retries_remaining=2 - attempt))
            if attempt == 2:
                raise
            messages.extend([dict(role='assistant', content=error.text), dict(role='user', content=
                'Your completed review is not valid JSON: ' + str(error) +
                '. Return the complete corrected JSON for the same target images. Preserve observed defects, '
                'uncertainty, target IDs and required fields; do not change verdicts to obtain valid syntax.')])
            continue
        try:
            return validator(response)
        except ValueError as error:
            write_json(Path(directory) / f"{name}-schema-failure-{attempt + 1}.json",
                dict(error=str(error), response=response, retries_remaining=2 - attempt))
            if attempt == 2:
                raise
            messages.extend([dict(role="assistant", content=json.dumps(response)),
                dict(role="user", content="Your review failed schema validation: " + str(error) +
                    ". Inspect the same images and return the complete corrected JSON. Do not silently "
                    "discard observed defects or change a failing verdict to pass to satisfy the schema. "
                    "issues contains visible defects only and must be [] for pass. State visibility limits "
                    "in observed_state; use indeterminate with a concrete correction when the requested "
                    "subject cannot be inspected. Preserve all required fields and target IDs.")])


def review_camera_batch(anchor, brief, targets, directory, identity=None, model='gpt-6.1-sol'):
    """Gate parent cameras before their geometry is propagated into later shots."""
    anchor = Path(anchor)
    contracts = {name: {key: row.get(key) for key in ('camera', 'state', 'focus', 'modules')}
                 for name, row in targets.items()}
    comparisons, comparison_hashes = comparison_context(targets)
    key = dict(anchor_sha256=digest(anchor), brief=brief, identity=identity,
        contracts=contracts, target_hashes={name: digest(Path(row['file'])) for name, row in targets.items()},
        prompt_sha256=sha256(PROMPT.encode()).hexdigest(),
        reinspection_prompt_sha256=sha256(REINSPECTION_PROMPT.encode()).hexdigest(),
        attachment_policy='embedded-labels.v1', model=model)
    if comparisons:
        key.update(comparison_hashes=comparison_hashes,
            comparison_prompt_sha256=sha256(COMPARISON_PROMPT.encode()).hexdigest())
    identity_hash = sha256(json.dumps(key, sort_keys=True).encode()).hexdigest()
    output = Path(directory) / identity_hash
    content = [dict(type='text', text=json.dumps(dict(brief=brief, identity_lock=identity, targets=contracts))),
               *image_content({'IDENTITY_ANCHOR': anchor}, label_images=True),
               *image_content({'TARGET_' + name: Path(row['file']) for name, row in targets.items()}, label_images=True),
               *image_content(comparisons, label_images=True)]
    rows = ask_validated_review([dict(role='system', content=PROMPT + ('\n' + COMPARISON_PROMPT if comparisons else '')), dict(role='user', content=content)],
        output, 'review', lambda response: validate_review(response, list(targets)), max_tokens=6000, model=model, stream=True,
        timeout=180, transient_retries=2, reuse_completed=True, reasoning_effort='medium')
    resolved, reinspections = reinspect_blockers(rows, anchor, brief, targets, output, identity, model)
    report = dict(schema='car2lego3d.reference-batch-review.v1', inputs=key,
        status='pass' if all(row['status'] == 'pass' for row in resolved.values()) else 'needs_revision',
        shots=resolved, reinspections=reinspections,
        appearance_accepted=False, physical_measurement_verified=False)
    report['reference_gate'] = assess_reference_gate(report['shots'])
    write_json(output / 'report.json', report)
    return report


def reusable_batch_reviews(manifest, images, root, output, model):
    """Reuse completed reviews only when their entire inspected input is unchanged."""
    expected = dict(anchor_sha256=digest(images['overview']), brief=manifest.get('brief'),
        identity=manifest.get('identity_lock'), prompt_sha256=sha256(PROMPT.encode()).hexdigest(),
        reinspection_prompt_sha256=sha256(REINSPECTION_PROMPT.encode()).hexdigest(),
        attachment_policy='embedded-labels.v1', model=model)
    records, sources, conflicts = {}, [], set()
    for path in sorted((Path(root) / 'batch-review').glob('*/report.json')):
        try:
            report = json.loads(path.read_text(encoding='utf-8'))
            inputs = report['inputs']
            if any(inputs.get(key) != value for key, value in expected.items()):
                continue
            names = set(inputs['target_hashes'])
            if not names or not names <= set(images) or set(inputs['contracts']) != names:
                continue
            _, comparison_hashes = comparison_context(comparison_targets(names, manifest['shots'], images))
            if (inputs.get('comparison_hashes', {}) != comparison_hashes or comparison_hashes and
                    inputs.get('comparison_prompt_sha256') != sha256(COMPARISON_PROMPT.encode()).hexdigest()):
                continue
            rows = validate_review(dict(shots=list(report['shots'].values())), list(names))
            if any(inputs['target_hashes'][name] != digest(images[name])
                   or inputs['contracts'][name] != {key: manifest['shots'][name].get(key)
                       for key in ('camera', 'state', 'focus', 'modules')}
                   or report['shots'][name]['sha256'] != inputs['target_hashes'][name] for name in names):
                continue
            signature = digest(path)
            destination = Path(output) / 'batch-evidence' / (signature + '.json')
            destination.parent.mkdir(exist_ok=True)
            if not destination.exists():
                shutil.copyfile(path, destination)
            if digest(destination) != signature:
                raise ValueError('Copied batch review hash differs')
            for row in rows:
                name = row['id']
                if name in records and any(records[name].get(key) != row.get(key)
                                           for key in ('status', 'issues', 'defects')):
                    conflicts.add(name)
                    records.pop(name)
                elif name not in conflicts:
                    records[name] = row
            sources.append(dict(file=str(destination.relative_to(output)), sha256=signature,
                views=sorted(names)))
        except (OSError, ValueError, KeyError, TypeError):
            continue
    return records, sources


def review_references(manifest_path, directory, model='gpt-6.1-sol'):
    directory = Path(directory)
    manifest, images = load_references(manifest_path)
    directory.mkdir(parents=True, exist_ok=True)
    snapshot = directory / 'reference-manifest.json'
    if snapshot.is_file() and digest(snapshot) != digest(manifest_path):
        raise ValueError('Review output belongs to different references; use a new directory')
    if not snapshot.is_file():
        shutil.copyfile(manifest_path, snapshot)
    shots = manifest.get('shots', manifest.get('views'))
    results, sources = reusable_batch_reviews(manifest, images, Path(manifest_path).parent, directory, model)
    reinspections = {}
    expected = [shot['id'] for shot in manifest.get('expected_shots', [])]
    names = [name for name in expected if name in images and name not in results]
    names.extend(sorted(set(images) - set(expected) - set(results)))
    for offset in range(0, len(names), 5):
        batch = names[offset:offset + 5]
        targets = comparison_targets(batch, shots, images)
        comparisons, _ = comparison_context(targets)
        contracts = {name: {key: shots[name].get(key) for key in ('camera', 'state', 'focus', 'modules')}
                     for name in batch}
        content = [{'type': 'text', 'text': json.dumps(dict(brief=manifest.get('brief'),
                   identity_lock=manifest.get('identity_lock'), targets=contracts))},
                   *image_content({'IDENTITY_ANCHOR': images['overview']}, label_images=True),
                   *image_content({'TARGET_' + name: images[name] for name in batch}, label_images=True),
                   *image_content(comparisons, label_images=True)]
        rows = ask_validated_review([{'role': 'system', 'content': PROMPT + ('\n' + COMPARISON_PROMPT if comparisons else '')}, {'role': 'user', 'content': content}],
            directory, f'review-{offset // 5 + 1}', lambda response: validate_review(response, batch),
            max_tokens=6000, model=model, stream=True,
            timeout=180, transient_retries=2, reuse_completed=True, reasoning_effort='medium')
        resolved, evidence = reinspect_blockers(rows, images['overview'], manifest.get('brief'),
            targets, directory,
            manifest.get('identity_lock'), model)
        results.update(resolved)
        reinspections.update(evidence)
    report = dict(schema='car2lego3d.reference-content-review.v1', model=model,
        manifest_sha256=digest(snapshot),
        manifest_snapshot=dict(file=snapshot.name, image_root=str(Path(manifest_path).resolve().parent)),
        status='pass' if all(
            item['status'] == 'pass' for item in results.values()) else 'needs_revision',
        shots=results, reinspections=reinspections, appearance_accepted=False, physical_measurement_verified=False)
    report['reused_batch_reviews'] = sources
    report['reference_gate'] = assess_reference_gate(results)
    write_json(directory / 'report.json', report)
    return report


def repair_corrections(report, previous=None):
    corrections = dict(previous or {})
    blocked = assess_reference_gate(report['shots'])['blocked_views']
    for name, result in report['shots'].items():
        if name in blocked:
            corrections[name] = result['correction'] + '\nReplace the rejected image with source SHA256 ' + result['sha256']
    return corrections
