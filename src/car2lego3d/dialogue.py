"""Audited text/vision dialogue using the original project's API configuration."""
import base64
from io import BytesIO
import json
import os
import time

from dotenv import dotenv_values
from PIL import Image, ImageDraw, ImageFont

from .common import ROOT, write_json
from .gpt_reference import configuration


def settings(model_key="OPENAI_MODEL", default_model="gpt-6.1-sol"):
    source, _ = configuration()
    inherited = dotenv_values(source / "backend/.env")
    local = dotenv_values(ROOT / ".env")
    def value(key, default=""):
        return os.environ.get(key) or local.get(key) or inherited.get(key) or default
    base = value("OPENAI_BASE_URL", "https://api.lundong.ai/v1").rstrip("/")
    return dict(api_key=value("OPENAI_API_KEY"), base_url=base if base.endswith("/v1") else base + "/v1",
                model=value(model_key, default_model))


def image_content(paths, *, label_images=False):
    content = []
    for label, path in paths.items():
        with Image.open(path) as image:
            image = image.convert("RGB")
            image.thumbnail((1200, 1200))
            if label_images:
                # Keep the identifier inside the pixels when a vision model reorders attachments.
                labelled = Image.new("RGB", (max(image.width, 480), image.height + 48), "white")
                labelled.paste(image, (0, 48))
                ImageDraw.Draw(labelled).text((12, 10), label, fill="black",
                    font=ImageFont.load_default(size=24))
                image = labelled
            stream = BytesIO()
            image.save(stream, format="JPEG", quality=92)
        content.extend([{"type": "text", "text": label}, {"type": "image_url", "image_url": {
            "url": "data:image/jpeg;base64," + base64.b64encode(stream.getvalue()).decode()}}])
    return content


def ask_json(messages, directory, name, max_tokens=16000, timeout=300, reuse_completed=False, *,
             model=None, stream=False, total_timeout=1800, reasoning_effort=None, transient_retries=0):
    from openai import OpenAI
    config = settings()
    if model is not None:
        config["model"] = model
    if not config["api_key"]:
        raise RuntimeError("OPENAI_API_KEY is missing from the original or experiment configuration.")
    # The gateway supports vision through Responses; validate plain JSON locally.
    inputs = []
    for message in messages:
        content = message["content"]
        if isinstance(content, list):
            content = [dict(type="input_text", text=c["text"]) if c["type"] == "text" else
                       dict(type="input_image", image_url=c["image_url"]["url"]) for c in content]
        inputs.append(dict(role=message["role"], content=content))
    request = dict(model=config["model"], input=inputs, max_output_tokens=max_tokens)
    if reasoning_effort is not None:
        request["reasoning"] = {"effort": reasoning_effort}
    if stream:
        request["stream"] = True
    directory.mkdir(parents=True, exist_ok=True)
    request_path = directory / f"{name}-request.json"
    response_path = directory / f"{name}-response.json"
    endpoint_path = directory / f"{name}-endpoint.json"
    endpoint = {"base_url": config["base_url"]}
    if reuse_completed and request_path.is_file() and response_path.is_file() and endpoint_path.is_file():
        if (json.loads(request_path.read_text(encoding="utf-8")) == request
                and json.loads(endpoint_path.read_text(encoding="utf-8")) == endpoint):
            raw = json.loads(response_path.read_text(encoding="utf-8"))
            if raw.get("status") == "completed":
                return parse_response(raw)
    # A failed replacement request must never pair its inputs with an older completed reply.
    response_path.unlink(missing_ok=True)
    write_json(directory / f"{name}-request.json", request)
    write_json(endpoint_path, endpoint)
    if timeout <= 0 or total_timeout <= 0:
        raise ValueError("Model timeouts must be positive")
    if isinstance(transient_retries, bool) or not isinstance(transient_retries, int) or transient_retries < 0:
        raise ValueError("transient_retries must be a nonnegative integer")
    started, attempts = time.monotonic(), []
    with OpenAI(**{k: v for k, v in config.items() if k != "model"}, timeout=timeout, max_retries=0) as client:
        for attempt in range(transient_retries + 1):
            remaining = total_timeout - (time.monotonic() - started)
            if remaining <= 0:
                raise TimeoutError("Model retry budget exhausted its total deadline")
            attempt_name = name if attempt == 0 else f"{name}-retry-{attempt}"
            try:
                raw = (streamed_response(client, request, directory, attempt_name, remaining) if stream
                       else client.responses.create(**request).model_dump(mode="json"))
                break
            except Exception as error:
                attempts.append(dict(name=attempt_name, error=type(error).__name__, message=str(error)[-2000:]))
                retry = attempt < transient_retries and transient_error(error)
                write_json(directory / f"{name}-retries.json", dict(attempts=attempts, retrying=retry))
                if not retry:
                    raise
                # A replacement is a fresh request; never execute text from an interrupted stream.
                if stream:
                    write_json(directory / f"{name}-progress.json", dict(status="retrying", model=config["model"],
                        attempt=attempt + 2, failed_attempt=attempt_name, error=type(error).__name__))
                time.sleep(min(2 ** attempt, 5))
        if attempts:
            write_json(directory / f"{name}-retries.json", dict(attempts=attempts,
                retrying=False, completed_attempt=attempt_name))
            if stream:
                progress = json.loads((directory / f"{attempt_name}-progress.json").read_text(encoding="utf-8"))
                write_json(directory / f"{name}-progress.json", {**progress, "completed_attempt": attempt_name})
    write_json(directory / f"{name}-response.json", raw)
    return parse_response(raw)


def streamed_response(client, request, directory, name, total_timeout):
    started = time.monotonic()
    progress_path = directory / f"{name}-progress.json"
    progress = dict(status="connecting", model=request["model"], events=0, output_characters=0, seconds=0)
    write_json(progress_path, progress)
    last_progress = 0
    response = None
    try:
        with (directory / f"{name}-events.jsonl").open("w", encoding="utf-8") as log:
            # Some compatible gateways omit response.created; require the authoritative terminal response.
            with client.responses.create(**request) as events:
                for event in events:
                    elapsed = time.monotonic() - started
                    progress.update(status="streaming", last_event=event.type, seconds=round(elapsed, 3),
                                    events=progress["events"] + 1)
                    if event.type == "response.output_text.delta":
                        progress["output_characters"] += len(event.delta)
                    log.write(json.dumps(event.model_dump(mode="json"), ensure_ascii=False) + "\n")
                    log.flush()
                    if getattr(event, "response", None):
                        progress["response_id"] = event.response.id
                        if event.type in {"response.completed", "response.failed", "response.incomplete"}:
                            raw = event.response.model_dump(mode="json")
                            # Gateways may omit required success fields in failure responses.
                            write_json(directory / f"{name}-response.json", raw)
                            if raw.get("status") != "completed":
                                details = raw.get("error") or raw.get("incomplete_details")
                                raise ModelResponseError(raw, details)
                            response = raw
                    if elapsed - last_progress >= .5 or event.type.startswith(("response.completed", "response.failed", "response.incomplete")):
                        write_json(progress_path, progress)
                        last_progress = elapsed
                    if elapsed > total_timeout:
                        raise TimeoutError(f"Model stream exceeded its {total_timeout}s total deadline")
                    if event.type == "error":
                        raise RuntimeError(f"Model stream error: {event.code}: {event.message}")
                    if response is not None:
                        break
        if response is None:
            raise ModelStreamInterrupted("Model stream ended without a terminal response; partial output cannot be used")
        progress.update(status=response["status"], seconds=round(time.monotonic() - started, 3))
        write_json(progress_path, progress)
        return response
    except Exception as error:
        progress.update(status="failed", error=type(error).__name__, seconds=round(time.monotonic() - started, 3))
        write_json(progress_path, progress)
        raise


class ModelResponseError(ValueError):
    def __init__(self, raw, details):
        super().__init__(f"Model response did not complete: {raw.get('status')}. {details}")
        self.raw = raw


class ModelStreamInterrupted(RuntimeError):
    pass


class ModelJSONError(ValueError):
    def __init__(self, text, error):
        super().__init__(f'Completed model text is invalid JSON: {error.msg} at line {error.lineno}, column {error.colno}')
        self.text = text


def transient_error(error):
    from openai import APIConnectionError, APIStatusError, APIError
    if isinstance(error, (APIConnectionError, ConnectionError, ModelStreamInterrupted)):
        return True
    if isinstance(error, APIStatusError):
        return error.status_code in {408, 409, 429} or error.status_code >= 500
    if isinstance(error, APIError):
        return str(error) == "stream_read_error"
    if isinstance(error, ModelResponseError):
        return error.raw.get("status") == "failed" and (error.raw.get("error") or {}).get("code") in {
            "upstream_error", "server_error", "rate_limit_exceeded"}
    return False


def parse_response(raw):
    if raw.get("status") != "completed":
        details = raw.get("error") or raw.get("incomplete_details")
        raise ValueError(f"Model response did not complete: {raw.get('status')}. {details}")
    # Validate the consumed text; compatible gateways may return null usage counters.
    texts = [part["text"] for item in (raw.get("output") or []) if item.get("type") == "message"
             for part in item.get("content", []) if part.get("type") == "output_text"]
    if not texts or any(not isinstance(text, str) for text in texts):
        raise ValueError("Completed model response has no valid output text")
    text = "".join(texts)
    try:
        return parse_json(text)
    except json.JSONDecodeError as error:
        raise ModelJSONError(text, error) from error


def parse_json(content):
    content = content or ""
    if content.lstrip().startswith("```"):
        content = "\n".join(content.strip().splitlines()[1:-1])
    return json.loads(content)
