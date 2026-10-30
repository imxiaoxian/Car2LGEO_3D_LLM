import json
import base64
from io import BytesIO
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from openai.types.responses import Response

from car2lego3d import dialogue
from car2lego3d.common import write_json


CONFIG = dict(api_key="test-only", base_url="https://api.example/v1", model="test-model")
MESSAGES = [dict(role="user", content="vehicle dimensions")]


def test_review_image_label_is_in_pixels_without_changing_the_reference(tmp_path):
    from PIL import Image
    from car2lego3d.common import digest
    path = tmp_path / 'front.png'
    Image.new('RGB', (640, 320), 'blue').save(path)
    original = digest(path)
    content = dialogue.image_content({'TARGET_front': path}, label_images=True)
    encoded = content[1]['image_url']['url'].split(',', 1)[1]
    with Image.open(BytesIO(base64.b64decode(encoded))) as labelled:
        assert labelled.size == (640, 368)
        assert labelled.crop((0, 0, 640, 48)).convert('L').getextrema()[0] < 30
        assert labelled.getpixel((320, 250))[2] > 240
    assert content[0]['text'] == 'TARGET_front' and digest(path) == original


def response(status="completed"):
    return Response.model_validate(dict(id="resp_test", created_at=1, model="test-model", object="response",
        status=status, output=[dict(id="msg_test", type="message", role="assistant", status="completed",
            content=[dict(type="output_text", text='{"length": 5103}', annotations=[])])],
        parallel_tool_calls=False, tool_choice="auto", tools=[]))


class Event:
    def __init__(self, event_type, response_obj=None, delta=None):
        self.type, self.response, self.delta = event_type, response_obj, delta

    def model_dump(self, mode="json"):
        result = {"type": self.type}
        if self.delta is not None:
            result["delta"] = self.delta
        if self.response is not None:
            result["response"] = self.response.model_dump(mode="json")
        return result


def stream_client(events):
    client = MagicMock()
    client.__enter__.return_value = client
    client.responses.create.return_value.__enter__.return_value = iter(events)
    return client


@pytest.mark.parametrize("change", [None, "endpoint", "model", "prompt", "incomplete"])
def test_resume_requires_identical_request_endpoint_and_completed_response(tmp_path, change):
    client = MagicMock()
    client.__enter__.return_value = client
    client.responses.create.return_value = response()
    with patch.object(dialogue, "settings", return_value=CONFIG.copy()) as settings, patch("openai.OpenAI", return_value=client):
        assert dialogue.ask_json(MESSAGES, tmp_path, "test") == {"length": 5103}
        assert "tools" not in client.responses.create.call_args.kwargs
        messages = MESSAGES
        if change == "endpoint": settings.return_value["base_url"] = "https://other.example/v1"
        if change == "model": settings.return_value["model"] = "other-model"
        if change == "prompt": messages = [dict(role="user", content="different vehicle")]
        if change == "incomplete": write_json(tmp_path / "test-response.json", response("incomplete").model_dump(mode="json"))
        assert dialogue.ask_json(messages, tmp_path, "test", reuse_completed=True) == {"length": 5103}
        assert client.responses.create.call_count == (1 if change is None else 2)


def test_incomplete_reply_is_retained_but_not_parsed(tmp_path):
    client = MagicMock()
    client.__enter__.return_value = client
    client.responses.create.return_value = response("incomplete")
    with patch.object(dialogue, "settings", return_value=CONFIG), patch("openai.OpenAI", return_value=client):
        with pytest.raises(ValueError, match="did not complete"):
            dialogue.ask_json(MESSAGES, tmp_path, "test")
    assert (tmp_path / "test-response.json").is_file()


def test_invalid_completed_json_retains_exact_text_for_bounded_schema_repair():
    raw = response().model_dump(mode='json')
    text = '{"shots":[{"id":"front" "status":"needs_revision"}]}'
    raw['output'][0]['content'][0]['text'] = text
    with pytest.raises(dialogue.ModelJSONError, match='invalid JSON') as error:
        dialogue.parse_response(raw)
    assert error.value.text == text


def test_explicit_model_is_used_and_invalidates_previous_model_cache(tmp_path):
    client = MagicMock()
    client.__enter__.return_value = client
    client.responses.create.return_value = response()
    with patch.object(dialogue, "settings", return_value=CONFIG.copy()), patch("openai.OpenAI", return_value=client):
        dialogue.ask_json(MESSAGES, tmp_path, "model", reuse_completed=True)
        dialogue.ask_json(MESSAGES, tmp_path, "model", reuse_completed=True, model="gpt-6-astra")
        assert client.responses.create.call_count == 2
        assert client.responses.create.call_args.kwargs["model"] == "gpt-6-astra"
        dialogue.ask_json(MESSAGES, tmp_path, "model", reuse_completed=True, model="gpt-6-astra")
        assert client.responses.create.call_count == 2


def test_modelling_setting_is_independent_from_vehicle_dialogue(monkeypatch, tmp_path):
    monkeypatch.setenv("OPENAI_MODEL", "vehicle-model")
    monkeypatch.delenv("CAR2LEGO_BLENDER_MODEL", raising=False)
    with patch.object(dialogue, "configuration", return_value=(tmp_path, "python")), \
            patch.object(dialogue, "dotenv_values", return_value={}):
        assert dialogue.settings()["model"] == "vehicle-model"
        assert dialogue.settings("CAR2LEGO_BLENDER_MODEL", "gpt-6-astra")["model"] == "gpt-6-astra"
        monkeypatch.setenv("CAR2LEGO_BLENDER_MODEL", "modelling-override")
        assert dialogue.settings("CAR2LEGO_BLENDER_MODEL", "gpt-6-astra")["model"] == "modelling-override"
        assert dialogue.settings()["model"] == "vehicle-model"


def test_streamed_reply_records_progress_until_terminal_event(tmp_path):
    completed = response()
    def events():
        yield Event("response.output_text.delta", delta='{"length": ')
        yield Event("response.completed", completed)
        raise AssertionError("A terminal event must finish without waiting for the connection to close")
    client = stream_client(events())
    with patch.object(dialogue, "settings", return_value=CONFIG.copy()), patch("openai.OpenAI", return_value=client):
        assert dialogue.ask_json(MESSAGES, tmp_path, "stream", model="gpt-6-astra", stream=True) == {"length": 5103}
    request = client.responses.create.call_args.kwargs
    assert request["stream"] is True
    progress = json.loads((tmp_path / "stream-progress.json").read_text(encoding="utf-8"))
    assert progress["status"] == "completed"
    assert progress["last_event"] == "response.completed"
    assert progress["events"] == 2
    assert len((tmp_path / "stream-events.jsonl").read_text(encoding="utf-8").splitlines()) == 2


def test_interrupted_stream_retains_partial_events_but_never_parses_them(tmp_path):
    def events():
        yield Event("response.output_text.delta", delta='{"length": 5103}')
        raise ConnectionError("Constructed connection reset")
    client = stream_client(events())
    with patch.object(dialogue, "settings", return_value=CONFIG.copy()), \
            patch("openai.OpenAI", return_value=client):
        with pytest.raises(ConnectionError), patch.object(dialogue, "parse_json") as parse:
            dialogue.ask_json(MESSAGES, tmp_path, "interrupted", stream=True)
        parse.assert_not_called()
    progress = json.loads((tmp_path / "interrupted-progress.json").read_text(encoding="utf-8"))
    assert progress["status"] == "failed"
    assert progress["events"] == 1
    assert not (tmp_path / "interrupted-response.json").exists()


def test_interrupted_replacement_cannot_reuse_an_older_completed_reply(tmp_path):
    client = stream_client([Event("response.completed", response())])
    with patch.object(dialogue, "settings", return_value=CONFIG.copy()), \
            patch("openai.OpenAI", return_value=client):
        dialogue.ask_json(MESSAGES, tmp_path, "model", stream=True, reuse_completed=True)
        client.responses.create.return_value.__enter__.side_effect = ConnectionError("Constructed timeout")
        for _ in range(2):
            with pytest.raises(ConnectionError):
                dialogue.ask_json(MESSAGES, tmp_path, "model", stream=True,
                                  reuse_completed=True, model="gpt-6-astra")
        assert client.responses.create.call_count == 3
    assert not (tmp_path / "model-response.json").exists()


@pytest.mark.parametrize("status", ["failed", "incomplete"])
def test_unsuccessful_terminal_stream_is_retained_without_parsing(tmp_path, status):
    client = stream_client([Event(f"response.{status}", response(status))])
    with patch.object(dialogue, "settings", return_value=CONFIG.copy()), \
            patch("openai.OpenAI", return_value=client), patch.object(dialogue, "parse_json") as parse:
        with pytest.raises(ValueError, match="did not complete"):
            dialogue.ask_json(MESSAGES, tmp_path, "terminal", stream=True)
        parse.assert_not_called()
    assert json.loads((tmp_path / "terminal-response.json").read_text(encoding="utf-8"))["status"] == status


def test_stream_without_terminal_event_fails_even_with_complete_json(tmp_path):
    client = stream_client([Event("response.output_text.delta", delta='{"length": 5103}')])
    with pytest.raises(RuntimeError, match="without a terminal response"):
        dialogue.streamed_response(client, {"model": "test-model"}, tmp_path, "missing", 1800)


def test_sparse_gateway_failure_preserves_the_original_error(tmp_path):
    raw = dict(id="resp_failed", status="failed", output=None,
               error=dict(code="upstream_error", message="Upstream request timed out"))
    failed = SimpleNamespace(id=raw["id"], model_dump=lambda **kwargs: raw)
    client = stream_client([Event("response.failed", failed)])
    with patch.object(dialogue, "settings", return_value=CONFIG.copy()), \
            patch("openai.OpenAI", return_value=client), patch.object(dialogue, "parse_json") as parse:
        with pytest.raises(ValueError, match="Upstream request timed out"):
            dialogue.ask_json(MESSAGES, tmp_path, "gateway", stream=True)
        parse.assert_not_called()
    assert json.loads((tmp_path / "gateway-response.json").read_text(encoding="utf-8")) == raw
    event = json.loads((tmp_path / "gateway-events.jsonl").read_text(encoding="utf-8"))
    assert event["response"]["error"]["code"] == "upstream_error"


def test_completed_gateway_reply_with_null_usage_can_be_reused(tmp_path):
    raw = response().model_dump(mode="json")
    raw["usage"] = {"input_tokens_details": {"cache_write_tokens": None}}
    completed = SimpleNamespace(id=raw["id"], model_dump=lambda **kwargs: raw)
    client = stream_client([Event("response.completed", completed)])
    with patch.object(dialogue, "settings", return_value=CONFIG.copy()), \
            patch("openai.OpenAI", return_value=client):
        for _ in range(2):
            assert dialogue.ask_json(MESSAGES, tmp_path, "gateway", stream=True,
                                     reuse_completed=True) == {"length": 5103}
        assert client.responses.create.call_count == 1
    assert json.loads((tmp_path / "gateway-response.json").read_text(encoding="utf-8")) == raw
    assert json.loads((tmp_path / "gateway-progress.json").read_text(encoding="utf-8"))["status"] == "completed"


def test_sparse_failed_cache_does_not_block_a_new_request(tmp_path):
    client = stream_client([Event("response.completed", response())])
    with patch.object(dialogue, "settings", return_value=CONFIG.copy()), \
            patch("openai.OpenAI", return_value=client):
        dialogue.ask_json(MESSAGES, tmp_path, "gateway", stream=True, reuse_completed=True)
        write_json(tmp_path / "gateway-response.json", dict(status="failed", output=None))
        client.responses.create.return_value.__enter__.return_value = iter([Event("response.completed", response())])
        assert dialogue.ask_json(MESSAGES, tmp_path, "gateway", stream=True,
                                 reuse_completed=True) == {"length": 5103}
        assert client.responses.create.call_count == 2


def test_active_stream_deadline_is_checked_and_failure_is_recorded(tmp_path):
    client = stream_client([Event("response.output_text.delta", delta="partial")])
    with patch.object(dialogue.time, "monotonic", side_effect=[0, 2, 2]):
        with pytest.raises(TimeoutError, match="total deadline"):
            dialogue.streamed_response(client, {"model": "test-model"}, tmp_path, "deadline", 1)
    progress = json.loads((tmp_path / "deadline-progress.json").read_text(encoding="utf-8"))
    assert progress["status"] == "failed"
    assert progress["error"] == "TimeoutError"


def test_stream_retry_keeps_failed_events_and_consumes_only_completed_reply(tmp_path):
    def interrupted():
        yield Event("response.output_text.delta", delta='{"incorrect_partial":true}')
        raise ConnectionError("Constructed reset")
    client = stream_client([])
    client.responses.create.return_value.__enter__.side_effect = [interrupted(),
        iter([Event("response.completed", response())])]
    with patch.object(dialogue, "settings", return_value=CONFIG.copy()), \
            patch("openai.OpenAI", return_value=client), patch.object(dialogue.time, "sleep"):
        assert dialogue.ask_json(MESSAGES, tmp_path, "model", stream=True, transient_retries=1) == {"length": 5103}
    assert "incorrect_partial" in (tmp_path / "model-events.jsonl").read_text()
    assert (tmp_path / "model-retry-1-events.jsonl").is_file()
    assert json.loads((tmp_path / "model-progress.json").read_text())["status"] == "completed"
    retries = json.loads((tmp_path / "model-retries.json").read_text())
    assert retries["completed_attempt"] == "model-retry-1"
    assert client.responses.create.call_count == 2


def test_incomplete_response_is_not_retried(tmp_path):
    client = stream_client([Event("response.incomplete", response("incomplete"))])
    with patch.object(dialogue, "settings", return_value=CONFIG.copy()), \
            patch("openai.OpenAI", return_value=client):
        with pytest.raises(ValueError, match="did not complete"):
            dialogue.ask_json(MESSAGES, tmp_path, "model", stream=True, transient_retries=2)
    assert client.responses.create.call_count == 1


def test_reasoning_setting_is_forwarded_and_part_of_cache_identity(tmp_path):
    client = MagicMock()
    client.__enter__.return_value = client
    client.responses.create.return_value = response()
    with patch.object(dialogue, "settings", return_value=CONFIG.copy()), patch("openai.OpenAI", return_value=client):
        dialogue.ask_json(MESSAGES, tmp_path, "model", reuse_completed=True)
        dialogue.ask_json(MESSAGES, tmp_path, "model", reuse_completed=True, reasoning_effort="medium")
        assert client.responses.create.call_count == 2
        assert client.responses.create.call_args.kwargs["reasoning"] == {"effort": "medium"}


def test_missing_terminal_event_retries_a_fresh_stream(tmp_path):
    client = stream_client([])
    client.responses.create.return_value.__enter__.side_effect = [
        iter([Event("response.output_text.delta", delta='{"not_authoritative":true}')]),
        iter([Event("response.completed", response())])]
    with patch.object(dialogue, "settings", return_value=CONFIG.copy()), \
            patch("openai.OpenAI", return_value=client), patch.object(dialogue.time, "sleep"):
        assert dialogue.ask_json(MESSAGES, tmp_path, "model", stream=True, transient_retries=1) == {"length": 5103}


def test_transient_upstream_failure_is_retained_and_retried(tmp_path):
    raw = dict(id="resp_failed", status="failed", output=None,
               error=dict(code="upstream_error", message="Constructed temporary outage"))
    failed = SimpleNamespace(id=raw["id"], model_dump=lambda **kwargs: raw)
    client = stream_client([])
    client.responses.create.return_value.__enter__.side_effect = [
        iter([Event("response.failed", failed)]), iter([Event("response.completed", response())])]
    with patch.object(dialogue, "settings", return_value=CONFIG.copy()), \
            patch("openai.OpenAI", return_value=client), patch.object(dialogue.time, "sleep"):
        assert dialogue.ask_json(MESSAGES, tmp_path, "model", stream=True, transient_retries=1) == {"length": 5103}
    assert "upstream_error" in (tmp_path / "model-events.jsonl").read_text()
    assert json.loads((tmp_path / "model-response.json").read_text())["status"] == "completed"
