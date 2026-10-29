import asyncio
from collections import Counter
import hashlib
import importlib.util
import json
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pytest
from PIL import Image

from car2lego3d.common import ROOT, write_json
from car2lego3d.reference_plan import Shot, reference_plan
from car2lego3d.references import load_references

spec = importlib.util.spec_from_file_location('rich_reference_worker', ROOT / 'scripts/car2lego_reference_worker.py')
worker = importlib.util.module_from_spec(spec)
spec.loader.exec_module(worker)


class Service:
    def __init__(self):
        self.calls = Counter()
        self.active = self.peak = 0
        self.fail = set()
        self.ReferenceImageService = lambda: SimpleNamespace(generate=self.generate)

    def design_directory(self, state):
        directory = Path(state['storage_path']) / 'car2lego'
        directory.mkdir(parents=True, exist_ok=True)
        return directory

    def transient_model_error(self, error):
        return False

    def failure_details(self, error, stage, attempt):
        return dict(error=str(error), attempt=attempt)

    def write_progress(self, *args):
        pass

    async def generate(self, state, only):
        name, = only
        directory = self.design_directory(state)
        path = directory / 'references/manifest.json'
        path.parent.mkdir(exist_ok=True)
        manifest = json.loads(path.read_text()) if path.exists() else dict(brief={}, images=[])
        request = state['requested_reference_shots'][0]
        cached = next((r for r in manifest['images'] if r['view'] == name), None)
        if cached and (directory / cached['path']).is_file():
            actual = hashlib.sha256((directory / cached['path']).read_bytes()).hexdigest()
            if cached['sha256'] == actual and cached['request'] == request:
                return dict(reference_images=manifest['images'])
        self.calls[name] += 1
        self.active += 1
        self.peak = max(self.peak, self.active)
        try:
            await asyncio.sleep(.005)
            if name in self.fail:
                raise ValueError('test failure')
            image = path.parent / (name + '.png')
            image.write_bytes(f'{name}-{self.calls[name]}'.encode())
            ref = dict(view=name, path='references/' + image.name,
                       sha256=hashlib.sha256(image.read_bytes()).hexdigest(), request=request)
            manifest['images'] = [r for r in manifest['images'] if r['view'] != name] + [ref]
            write_json(path, manifest)
            return dict(reference_images=manifest['images'])
        finally:
            self.active -= 1


def run(service, directory, shots):
    return asyncio.run(worker.generate_reference_set(service,
        dict(storage_path=str(directory), design_id='car2lego'), write_json, shots, attempts=1))


def test_dependency_cache_and_bounded_concurrency(tmp_path):
    service = Service()
    shots = reference_plan('Countach')
    run(service, tmp_path, shots)
    assert len(shots) == 30 and service.peak == 5
    initial = service.calls.copy()
    run(service, tmp_path, shots)
    assert service.calls == initial
    (tmp_path / 'car2lego/references/left.png').unlink()
    run(service, tmp_path, shots)
    assert service.calls['left'] == 2
    assert service.calls['door_left_open'] == 2
    assert service.calls['scissor_hinge'] == 2
    assert service.calls['right'] == 1 and service.calls['overview'] == 1


def test_provider_concurrency_limit_cannot_be_exceeded(tmp_path):
    with pytest.raises(ValueError, match='between 1 and 5'):
        asyncio.run(worker.generate_reference_set(Service(),
            dict(storage_path=str(tmp_path), design_id='car2lego'), write_json,
            reference_plan(), concurrency=6))


def test_intermediate_scissor_pose_uses_closed_parent_instead_of_upright_pose():
    shots = {shot.id: shot for shot in reference_plan('Countach')}
    hinge = shots['scissor_hinge']
    assert hinge.parents == ('overview', 'door_left_closed')
    assert '35 degrees' in hinge.state and 'not upright' in hinge.state
    assert 'not a measured mechanism limit' in hinge.focus
    assert 'fully open' in shots['door_left_open'].state


def test_open_doors_use_closed_same_side_reference_and_preserve_handedness():
    shots = {shot.id: shot for shot in reference_plan('Countach')}
    right = shots['door_right_open']
    assert right.parents == ('overview', 'door_right_closed', 'right', 'front_right')
    assert 'nose points image RIGHT' in right.camera
    assert 'left door closed' in right.state
    assert 'steering wheel stays on the FAR side' in right.focus
    assert 'never mirror the cabin' in right.focus
    left = shots['door_left_open']
    assert 'door_left_closed' in left.parents
    assert 'nose points image LEFT' in left.camera


def test_baseline_cabin_references_keep_visible_seats_without_hidden_hardware_shots(tmp_path):
    shots = reference_plan('Countach')
    by_id = {shot.id: shot for shot in shots}
    service = Service()
    run(service, tmp_path, shots)
    manifest = json.loads((tmp_path / 'car2lego/references/manifest.json').read_text())
    records = {item['view']: item for item in manifest['images']}
    assert not {'pedal_footwell', 'seat_mounts'} & records.keys()
    assert 'passenger_seat' in records
    assert all(by_id[name].modules == ('interior', 'cabin')
               for name in ('seats_pedals', 'passenger_seat'))
    assert 'driver seat cushion' in by_id['seats_pedals'].focus
    assert 'passenger seat cushion' in by_id['passenger_seat'].focus
    assert set(by_id['passenger_seat'].parents) == {'overview', 'door_right_open'}
    assert all('looking REARWARDS' in by_id[name].camera for name in ('seats_pedals', 'passenger_seat'))
    assert 'knowledge-base IO' in by_id['seats_pedals'].focus
    assert all(records[parent]['sha256'] == expected
               for name in ('seats_pedals', 'passenger_seat')
               for parent, expected in records[name]['parents'].items())


def test_non_baseline_plan_preserves_existing_detail_cameras():
    shots = {shot.id: shot for shot in reference_plan('BMW M4')}
    assert {'pedal_footwell', 'seat_mounts'} <= shots.keys()
    assert 'covered attachments remain uncertain' in shots['seat_mounts'].focus


def test_identity_finishes_before_dependents_and_invalidates_their_cache(tmp_path):
    service = Service()
    completed = []
    observed = dict(paint="white", interior="cream")
    original = service.generate

    async def generate(state, only):
        if only != {"overview"}:
            assert completed and state["identity_lock"]["observations"] == observed
        return await original(state, only)

    async def read_identity(anchor, brief):
        assert anchor.is_file() and service.active == 0
        completed.append(True)
        return dict(observations=dict(observed))

    service.generate = generate
    shots = reference_plan()
    for paint in ("white", "blue"):
        observed["paint"] = paint
        asyncio.run(worker.generate_reference_set(service,
            dict(storage_path=str(tmp_path), design_id="car2lego"), write_json,
            shots, attempts=1, identity_reader=read_identity))
    assert service.calls["overview"] == 1
    assert all(service.calls[shot.id] == 2 for shot in shots if shot.id != "overview")
    manifest = json.loads((tmp_path / "car2lego/references/manifest.json").read_text())
    assert manifest["identity_lock"]["observations"]["paint"] == "blue"


def test_identity_audit_changes_reuse_images_but_visible_repairs_invalidate(tmp_path):
    from copy import deepcopy
    identity = dict(anchor_sha256='pixels', observations=dict(paint='white'),
        reference_gate=dict(repair_tasks=[dict(id='audit-id', kind='rim_geometry', modules=['front_wheel'],
            phase_modules=['body'], expected='five holes', verification='count large holes',
            status='open', evidence=[])]))
    service = Service()
    shots = [Shot('overview', 'anchor', (), parents=()), Shot('left', 'side', (), parents=('overview',))]

    async def read_identity(anchor, brief):
        return deepcopy(identity)

    def execute():
        asyncio.run(worker.generate_reference_set(service,
            dict(storage_path=str(tmp_path), design_id='car2lego'), write_json,
            shots, attempts=1, identity_reader=read_identity))

    execute()
    identity.update(prompt_sha256='changed review policy', model='changed reviewer')
    identity['reference_gate']['repair_tasks'][0].update(
        id='new-audit-id', phase_modules=['wheels'], status='closed', evidence=['new report'])
    execute()
    assert service.calls == {'overview': 1, 'left': 1}
    identity['reference_gate']['repair_tasks'][0]['expected'] = 'six holes'
    execute()
    assert service.calls == {'overview': 1, 'left': 2}


def test_failed_parent_is_repaired_before_dependent_dispatch_and_passed_siblings_are_reused(tmp_path):
    service = Service()
    shots = [Shot("overview", "anchor", (), parents=()),
             Shot("side", "side", (), parents=("overview",)),
             Shot("top", "top", (), parents=("overview",)),
             Shot("detail", "closeup", (), parents=("side",))]
    original = service.generate

    async def generate(state, only):
        if only == {"detail"}:
            assert service.calls["side"] == 2
        return await original(state, only)

    service.generate = generate

    async def review(anchor, brief, targets, identity):
        return dict(shots={name: dict(status="needs_revision" if name == "side" and service.calls[name] == 1 else "pass",
            correction="Restore cream interior") for name in targets})

    def execute():
        asyncio.run(worker.generate_reference_set(service, dict(storage_path=str(tmp_path), design_id="car2lego"),
            write_json, shots, attempts=1, batch_reviewer=review))

    execute()
    assert service.calls == {"overview": 1, "side": 2, "top": 1, "detail": 1}
    initial = service.calls.copy()
    execute()
    assert service.calls == initial
    checkpoint = json.loads((tmp_path / "car2lego/reference-batch-corrections.json").read_text())
    assert "Restore cream interior" in checkpoint["corrections"]["side"]


def test_anchor_revision_is_reviewed_before_any_dependent_request(tmp_path):
    service = Service()
    shots = [Shot("overview", "anchor", (), parents=()), Shot("side", "side", (), parents=("overview",))]
    original = service.generate

    async def generate(state, only):
        if only == {"side"}:
            assert service.calls["overview"] == 2
        return await original(state, only)

    async def read(anchor, brief):
        rejected = service.calls["overview"] == 1
        return dict(observations=dict(wheels="six" if rejected else "five"), anchor_review=dict(
            status="needs_revision" if rejected else "pass", correction="Use five large rim openings"))

    service.generate = generate
    asyncio.run(worker.generate_reference_set(service, dict(storage_path=str(tmp_path), design_id="car2lego"),
        write_json, shots, attempts=1, identity_reader=read))
    assert service.calls == {"overview": 2, "side": 1}
    assert len(list((tmp_path / "car2lego/references/history").glob("overview-*.png"))) == 1
    manifest = json.loads((tmp_path / "car2lego/references/manifest.json").read_text())
    assert manifest["identity_lock"]["observations"]["wheels"] == "five"


def test_local_rim_debt_does_not_regenerate_anchor_or_block_children(tmp_path):
    service = Service()
    shots = [Shot("overview", "anchor", (), parents=()), Shot("side", "side", (), parents=("overview",)),
        Shot("detail", "closeup", (), parents=("side",))]
    def row():
        return dict(status="needs_revision", issues=["Wrong rim hole count"], correction="Use five holes",
            defects=[dict(issue_index=0, kind="rim_geometry", modules=["wheels"], expected="five holes",
                verification="inspect final rim geometry")])
    async def read(anchor, brief):
        return dict(observations=dict(wheels="six visible holes"), anchor_review=row())
    async def review(anchor, brief, targets, identity):
        return dict(shots={name: row() for name in targets})
    asyncio.run(worker.generate_reference_set(service, dict(storage_path=str(tmp_path), design_id="car2lego"),
        write_json, shots, attempts=1, identity_reader=read, batch_reviewer=review))
    assert service.calls == {"overview": 1, "side": 1, "detail": 1}
    manifest = json.loads((tmp_path / "car2lego/references/manifest.json").read_text())
    assert manifest["identity_lock"]["anchor_review"]["status"] == "needs_revision"
    assert manifest["identity_lock"]["observations"]["wheels"] == "six visible holes"


def test_batch_review_exhaustion_preserves_siblings_and_never_dispatches_children(tmp_path):
    service = Service()
    shots = [Shot("overview", "anchor", (), parents=()),
             Shot("side", "side", (), parents=("overview",)),
             Shot("top", "top", (), parents=("overview",)),
             Shot("detail", "closeup", (), parents=("side",))]

    async def reject(anchor, brief, targets, identity):
        return dict(shots={name: dict(status="needs_revision" if name == "side" else "pass",
                                     correction="Restore opaque roof") for name in targets})

    with pytest.raises(ValueError, match="algorithm diagnosis"):
        asyncio.run(worker.generate_reference_set(service, dict(storage_path=str(tmp_path), design_id="car2lego"),
            write_json, shots, attempts=1, batch_reviewer=reject, max_batch_repairs=2))
    assert service.calls == {"overview": 1, "side": 3, "top": 1}
    dispatch = json.loads((tmp_path / "car2lego/reference-dispatch.json").read_text())
    assert dispatch["status"] == "failed" and dispatch["failed_views"] == ["side"]
    assert dispatch["failed_details"]["side"]["exception_type"] == "ReferenceContentError"
    assert dispatch["failed_details"]["side"]["attempts"] == 3
    assert (tmp_path / "car2lego/references/top.png").is_file()


def test_newly_unblocked_shots_fill_next_batch_after_whole_batch_barrier(tmp_path):
    shots = [Shot("overview", "anchor", (), parents=()),
             *(Shot(f"side_{index}", "side", (), parents=("overview",)) for index in range(6)),
             Shot("detail", "closeup", (), parents=("side_0",))]
    run(Service(), tmp_path, shots)
    record = json.loads((tmp_path / "car2lego/reference-dispatch.json").read_text())
    assert [batch["views"] for batch in record["batches"]] == [
        ["overview"], [f"side_{index}" for index in range(5)], ["side_5", "detail"]]
    assert all(batch["status"] == "completed" for batch in record["batches"])


def test_failed_review_records_batch_failure_and_preserves_generated_pixels(tmp_path):
    service = Service()
    shots = [Shot('overview', 'anchor', (), parents=()),
             Shot('left', 'side', (), parents=('overview',)),
             Shot('detail', 'detail', (), parents=('left',))]

    async def invalid_review(*args):
        raise ValueError('Malformed review IDs')

    with pytest.raises(ValueError, match='Malformed review IDs'):
        asyncio.run(worker.generate_reference_set(service, dict(storage_path=str(tmp_path), design_id='car2lego'),
            write_json, shots, attempts=1, batch_reviewer=invalid_review))
    dispatch = json.loads((tmp_path / 'car2lego/reference-dispatch.json').read_text())
    assert dispatch['status'] == dispatch['batches'][-1]['status'] == 'failed'
    assert dispatch['failed_views'] == ['left']
    assert dispatch['failed_details']['left']['stage'] == 'reference_content_review'
    assert dispatch['failed_details']['left']['error'] == 'Malformed review IDs'
    assert service.calls == {'overview': 1, 'left': 1}
    assert (tmp_path / 'car2lego/references/left.png').is_file()


@pytest.mark.parametrize('retry', [False, True])
def test_next_batch_waits_for_slowest_request_including_retries(tmp_path, retry):
    class TemporaryFailure(Exception):
        pass

    class GatedService(Service):
        def __init__(self):
            super().__init__()
            self.started = set()
            self.completed = set()
            self.release_slow = asyncio.Event()
            self.slow_started = asyncio.Event()
            self.fast_completed = asyncio.Event()
            self.slow_attempts = 0

        def transient_model_error(self, error):
            return isinstance(error, TemporaryFailure)

        async def generate(self, state, only):
            name, = only
            self.started.add(name)
            if name == 'top':
                self.slow_attempts += 1
                if retry and self.slow_attempts == 1:
                    raise TemporaryFailure('retry this image')
                self.slow_started.set()
                await self.release_slow.wait()
            result = await super().generate(state, only)
            self.completed.add(name)
            if {'front', 'back', 'left', 'right'} <= self.completed:
                self.fast_completed.set()
            return result

    async def exercise():
        service = GatedService()
        task = asyncio.create_task(worker.generate_reference_set(service,
            dict(storage_path=str(tmp_path), design_id='car2lego'), write_json,
            reference_plan(), attempts=2, retry_delay=0))
        try:
            await service.slow_started.wait()
            await service.fast_completed.wait()
            # Give freed semaphore slots time to admit another request if the
            # scheduler lacks a batch barrier. The fifth image is still held.
            await asyncio.sleep(.03)
            assert service.started == {'overview', 'front', 'back', 'left', 'right', 'top'}
            assert service.slow_attempts == (2 if retry else 1)
            dispatch = json.loads((tmp_path / 'car2lego/reference-dispatch.json').read_text())
            assert dispatch['status'] == 'running' and dispatch['batch_index'] == 2
            assert dispatch['views'] == ['front', 'back', 'left', 'right', 'top']
            assert set(dispatch['completed_views']) == {'overview', 'front', 'back', 'left', 'right'}
            assert dispatch['batches'][-1]['completed_views'] == ['front', 'back', 'left', 'right']
            assert len(dispatch['batches']) == 2
            service.release_slow.set()
            result = await task
            assert len(result['reference_images']) == len(reference_plan())
            dispatch = json.loads((tmp_path / 'car2lego/reference-dispatch.json').read_text())
            assert dispatch['status'] == 'completed'
            assert len(dispatch['completed_views']) == len(reference_plan())
            batches = dispatch['batches']
            assert all(b['status'] == 'completed' and len(b['views']) <= 5 for b in batches)
            assert all(set(b['views']) == set(b['completed_views']) for b in batches)
            assert all(datetime.fromisoformat(previous['finished_at']) <= datetime.fromisoformat(next_['started_at'])
                       for previous, next_ in zip(batches, batches[1:]))
        finally:
            service.release_slow.set()
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    asyncio.run(asyncio.wait_for(exercise(), timeout=5))


def test_completed_siblings_survive_failure(tmp_path):
    service = Service()
    service.fail.add('left')
    shots = reference_plan()
    with pytest.raises(ValueError, match='test failure'):
        run(service, tmp_path, shots)
    assert service.calls['right'] == 1
    assert service.calls['top'] == 1
    assert service.calls['front_left'] == 0
    dispatch = json.loads((tmp_path / 'car2lego/reference-dispatch.json').read_text())
    assert dispatch['status'] == 'failed' and dispatch['failed_views'] == ['left']
    assert dispatch['failed_details']['left']['error'] == 'test failure'
    assert dispatch['failed_details']['left']['attempt'] == 1
    assert len(dispatch['batches']) == 2
    assert dispatch['batches'][-1]['status'] == 'failed'
    assert set(dispatch['batches'][-1]['completed_views']) == {'front', 'back', 'right', 'top'}
    service.fail.clear()
    run(service, tmp_path, shots)
    assert service.calls['right'] == 1 and service.calls['left'] == 2
    dispatch = json.loads((tmp_path / 'car2lego/reference-dispatch.json').read_text())
    assert dispatch['failed_details'] == {}


def test_rich_manifest_rejects_stale_parent_and_keeps_module_details(tmp_path):
    records = {}
    for i, shot in enumerate(reference_plan()):
        file = tmp_path / f'{shot.id}.png'
        Image.new('RGB', (20, 20), (i, 30, 40)).save(file)
        records[shot.id] = {**shot.record(), 'file': file.name,
                            'sha256': hashlib.sha256(file.read_bytes()).hexdigest()}
    for record in records.values():
        record['parents'] = {p: records[p]['sha256'] for p in record['parents']}
    path = tmp_path / 'views.json'
    manifest = dict(schema_version=2, shots=records, expected_shots=[s.record() for s in reference_plan()],
                    views={v: records[v] for v in ('front', 'back', 'left', 'right')})
    write_json(path, manifest)
    _, images = load_references(path, ['doors'])
    assert {'overview', 'door_left_open', 'door_right_closed'} <= images.keys()
    records['door_left_open']['parents']['left'] = 'bad'
    write_json(path, manifest)
    with pytest.raises(ValueError, match='Stale parent'):
        load_references(path)


def test_related_right_side_images_precede_the_left_front_identity_anchor(tmp_path, monkeypatch):
    calls = []

    async def edit(**request):
        calls.append(dict(files=[Path(file.name).name for file in request['image']], prompt=request['prompt']))
        assert all(not file.closed for file in request['image'])
        return 'result'

    async def generate(**request):
        raise AssertionError('An edit must preserve actual reference inputs')

    @asynccontextmanager
    async def slot():
        yield

    monkeypatch.setattr(worker, 'image_slot', slot)
    service = SimpleNamespace(ALL_REFERENCE_VIEWS={}, REFERENCE_VIEWS={}, DETAIL_VIEWS={},
        AsyncOpenAI=lambda *args, **kwargs: SimpleNamespace(images=SimpleNamespace(edit=edit, generate=generate)))
    context, prompts = worker.configure_shots(service, reference_plan())
    for name in ('overview', 'right', 'front_right', 'identity-anchor'):
        (tmp_path / (name + '.png')).write_bytes(name.encode())
    scratch = tmp_path / 'worker-cache'
    scratch.mkdir()
    (scratch / 'overview.png').write_bytes(b'overview')

    async def exercise():
        context.set(('door_right_open', [tmp_path / (name + '.png') for name in ('overview', 'right', 'front_right')]))
        with (scratch / 'overview.png').open('rb') as anchor, (tmp_path / 'identity-anchor.png').open('rb') as source:
            assert await service.AsyncOpenAI().images.edit(image=[anchor, source], prompt='Original service prompt') == 'result'

    asyncio.run(exercise())
    assert calls[0]['files'] == ['right.png', 'front_right.png', 'overview.png', 'identity-anchor.png']
    assert 'image 1: right' in calls[0]['prompt']
    assert calls[0]['prompt'].index('ATTACHED REFERENCE IDENTITIES') < calls[0]['prompt'].index('FINAL SHOT CONTRACT')
    assert prompts['door_right_open'] == calls[0]['prompt']


def test_transport_failure_retains_safe_payload_fingerprint_and_elapsed_time(tmp_path, monkeypatch):
    async def disconnect(**request):
        raise ConnectionError('provider disconnected; credential must not appear in audit')

    @asynccontextmanager
    async def slot():
        yield

    monkeypatch.setattr(worker, 'image_slot', slot)
    service = SimpleNamespace(ALL_REFERENCE_VIEWS={}, REFERENCE_VIEWS={}, DETAIL_VIEWS={},
        AsyncOpenAI=lambda *args, **kwargs: SimpleNamespace(images=SimpleNamespace(edit=disconnect, generate=disconnect)))
    context, _ = worker.configure_shots(service, reference_plan('Countach'))
    source = tmp_path / 'rear_left.png'
    source.write_bytes(b'immutable reference pixels')

    async def exercise():
        token = worker.state_storage.set(str(tmp_path / 'diagnostics'))
        camera = context.set(('side_intake', [source]))
        try:
            with source.open('rb') as image:
                with pytest.raises(ConnectionError):
                    await service.AsyncOpenAI(api_key='secret-api-key').images.edit(
                        image=[image], prompt='private prompt', model='gpt-image-fixture', quality='high', size='auto')
        finally:
            context.reset(camera)
            worker.state_storage.reset(token)

    asyncio.run(exercise())
    path = tmp_path / 'diagnostics/reference-transport.jsonl'
    records = [json.loads(line) for line in path.read_text().splitlines()]
    assert [row['status'] for row in records] == ['started', 'failed']
    assert len({row['request_id'] for row in records}) == 1
    assert records[0]['view'] == 'side_intake' and records[0]['prompt_sha256']
    assert records[0]['inputs'] == [dict(name=source.name, bytes=source.stat().st_size,
        sha256=hashlib.sha256(source.read_bytes()).hexdigest())]
    assert records[1]['seconds'] >= 0 and records[1]['exception_type'] == 'ConnectionError'
    assert 'secret-api-key' not in path.read_text() and 'private prompt' not in path.read_text()


def test_rejected_anchor_is_edited_from_its_immutable_pixels(tmp_path, monkeypatch):
    calls = []

    async def edit(**request):
        calls.append([Path(file.name).read_bytes() for file in request['image']])
        return 'edited'

    async def generate(**request):
        raise AssertionError('Anchor repair must preserve the rejected vehicle identity')

    @asynccontextmanager
    async def slot():
        yield

    monkeypatch.setattr(worker, 'image_slot', slot)
    service = SimpleNamespace(ALL_REFERENCE_VIEWS={}, REFERENCE_VIEWS={}, DETAIL_VIEWS={},
        AsyncOpenAI=lambda *args, **kwargs: SimpleNamespace(images=SimpleNamespace(edit=edit, generate=generate)))
    context, _ = worker.configure_shots(service, reference_plan())
    anchor = tmp_path / 'overview-rejected.png'
    anchor.write_bytes(b'original-white-cream-car')

    async def exercise():
        context.set(('overview', [anchor]))
        assert await service.AsyncOpenAI().images.generate(prompt='Correct only the rim opening count') == 'edited'

    asyncio.run(exercise())
    assert calls == [[b'original-white-cream-car']]
