"""Loopback-only planner inference. Website remains the assembly tool executor."""
from __future__ import annotations

import argparse
import gc
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import threading
import time
import uuid

from common import MODEL_ID, REVISION, configure_cache, file_hash, read_json
from gpu_lock import GPULock


class RequestError(ValueError):
    pass


def validate_request(body, expected_name):
    if not isinstance(body, dict):
        raise RequestError("Expected a JSON object")
    if body.get("model") != expected_name:
        raise RequestError("The requested model does not match this service")
    messages = body.get("messages")
    if not isinstance(messages, list) or not messages or len(messages) > 16:
        raise RequestError("Provide 1-16 messages")
    for message in messages:
        if not isinstance(message, dict) or message.get("role") not in {"system", "user", "assistant"} or not isinstance(message.get("content"), str):
            raise RequestError("Only text chat messages are supported")
    if sum(len(m["content"]) for m in messages) > 4_000_000:
        raise RequestError("Input exceeds the service text limit")
    response_format = body.get("response_format")
    if not isinstance(response_format, dict) or not isinstance(response_format.get("json_schema"), dict):
        raise RequestError("A JSON decision schema is required")
    schema = response_format["json_schema"].get("schema")
    if response_format.get("type") != "json_schema" or not isinstance(schema, dict):
        raise RequestError("A JSON decision schema is required")
    if set(schema.get("properties", {})) != {"tool", "arguments_json", "reason"}:
        raise RequestError("Only the assembly decision schema is supported")
    limit = body.get("max_tokens", 256)
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 4096:
        raise RequestError("max_tokens must be in 1..4096")
    if body.get("temperature", 0) != 0:
        raise RequestError("Comparison uses deterministic greedy generation (temperature=0)")
    return messages, schema, limit


def validate_adapter(path):
    path = Path(path).resolve()
    manifest = read_json(path.parent / "training-manifest.json")
    if manifest.get("model_id") != MODEL_ID or manifest.get("revision") != REVISION:
        raise ValueError("Adapter does not match the pinned base revision")
    if manifest.get("status") != "trained_not_accepted" or not manifest.get("samples", 0):
        raise ValueError("Adapter lacks an actual training manifest")
    steps = manifest.get("optimizer_steps")
    if isinstance(steps, bool) or not isinstance(steps, int) or steps < 1:
        raise ValueError("Adapter requires at least one recorded optimizer step")
    files = manifest.get("adapter_files", {})
    required = {"adapter_config.json", "adapter_model.safetensors"}
    if not required.issubset(files):
        raise ValueError("Adapter dependency hashes must include its LoRA config and safetensors")
    actual = {str(p.relative_to(path)) for p in path.rglob("*") if p.is_file()}
    if actual != set(files):
        raise ValueError("Adapter dependency inventory does not match its manifest")
    for relative, digest in files.items():
        dependency = (path / relative).resolve()
        if not dependency.is_relative_to(path) or file_hash(dependency) != digest:
            raise ValueError("Adapter dependency integrity failed")
    adapter_config = read_json(path / "adapter_config.json")
    if adapter_config.get("peft_type") != "LORA" or adapter_config.get("base_model_name_or_path") != MODEL_ID:
        raise ValueError("Adapter config does not describe LoRA for the pinned base model")
    return manifest


class Runtime:
    def __init__(self, *, adapter=None, max_context=1024):
        if isinstance(max_context, bool) or not isinstance(max_context, int) or not 1 <= max_context <= 32768:
            raise ValueError("max_context must be in 1..32768 for the pinned base model")
        self.name = "local_finetuned" if adapter else "local_base"
        self.adapter = Path(adapter).resolve() if adapter else None
        self.manifest = validate_adapter(self.adapter) if self.adapter else None
        self.max_context = max_context
        self.semaphore = threading.BoundedSemaphore(1)
        self.adapter_manifest_sha256 = file_hash(self.adapter.parent / "training-manifest.json") if self.adapter else None
        self.last_inference = None

    def identity(self):
        adapter = None
        if self.manifest:
            adapter = dict(path=str(self.adapter), manifest_sha256=self.adapter_manifest_sha256,
                status=self.manifest["status"], training_samples=self.manifest["samples"],
                optimizer_steps=self.manifest["optimizer_steps"], actual_epochs=self.manifest.get("actual_epochs"),
                dataset_sha256=self.manifest.get("dataset_sha256"), files=self.manifest["adapter_files"])
        return dict(status="listening_not_accepted", model=self.name, base_model=MODEL_ID, revision=REVISION,
            max_context=self.max_context, max_completion_tokens=4096, adapter=adapter,
            inference_verified=False, quality_accepted=False,
            gpu_policy="load_inside_cooperative_lock_unload_after_request")

    def check_context(self, prompt_tokens, max_new_tokens):
        if prompt_tokens + max_new_tokens > self.max_context:
            raise RequestError(f"Context limit exceeded: input_tokens={prompt_tokens}, "
                f"requested_output_tokens={max_new_tokens}, max_context={self.max_context}; no silent truncation")

    def check_adapter_identity(self):
        if self.adapter:
            current = file_hash(self.adapter.parent / "training-manifest.json")
            if current != self.adapter_manifest_sha256:
                raise ValueError("Adapter manifest changed after service startup; restart with the intended model")
            validate_adapter(self.adapter)

    def infer(self, body):
        messages, schema, max_new_tokens = validate_request(body, self.name)
        if not self.semaphore.acquire(blocking=False):
            raise BlockingIOError("Model is busy; retry this decision")
        self.last_inference = dict(generation_attempted=False, generation_completed=False)
        model = tokenizer = tokens = output = generated = None
        try:
            configure_cache()
            from jsonschema import Draft202012Validator
            from transformers import AutoModelForCausalLM, AutoTokenizer
            tokenizer = AutoTokenizer.from_pretrained(MODEL_ID, revision=REVISION, trust_remote_code=False, local_files_only=True)
            ids = tokenizer.apply_chat_template(messages, tokenize=True, add_generation_prompt=True, return_tensors="pt")
            prompt_tokens = int(ids.shape[1])
            self.check_context(prompt_tokens, max_new_tokens)
            self.last_inference.update(prompt_tokens=prompt_tokens, requested_output_tokens=max_new_tokens)
            self.check_adapter_identity()
            import torch
            if not torch.cuda.is_available() or not torch.cuda.is_bf16_supported():
                raise RuntimeError("BF16 CUDA is required")
            # CPU remains idle outside the lock; GPU weights are released per request.
            failure = None
            result = None
            with GPULock(purpose="inference", timeout=600):
                try:
                    model = AutoModelForCausalLM.from_pretrained(MODEL_ID, revision=REVISION, trust_remote_code=False,
                        local_files_only=True, torch_dtype=torch.bfloat16, attn_implementation="sdpa")
                    if self.adapter:
                        from peft import PeftModel
                        model = PeftModel.from_pretrained(model, str(self.adapter), is_trainable=False)
                    model.to("cuda").eval()
                    tokens = ids.to("cuda")
                    self.last_inference["generation_attempted"] = True
                    with torch.inference_mode():
                        output = model.generate(input_ids=tokens, attention_mask=torch.ones_like(tokens), do_sample=False,
                            max_new_tokens=max_new_tokens, pad_token_id=tokenizer.eos_token_id)
                    generated = output[0, prompt_tokens:]
                    content = tokenizer.decode(generated, skip_special_tokens=True).strip()
                    self.last_inference.update(generation_completed=True,
                        completion_tokens=int(generated.shape[0]), raw_content=content)
                    end_tokens = model.generation_config.eos_token_id
                    end_tokens = {end_tokens} if isinstance(end_tokens, int) else set(end_tokens or [tokenizer.eos_token_id])
                    if not generated.numel() or int(generated[-1]) not in end_tokens:
                        raise ValueError("Model exhausted its output budget before completing the decision")
                    try:
                        decision = json.loads(content)
                    except json.JSONDecodeError:
                        raise ValueError("Model did not return a complete JSON assembly decision") from None
                    Draft202012Validator(schema).validate(decision)
                    arguments = json.loads(decision["arguments_json"])
                    if not isinstance(arguments, dict):
                        raise ValueError("Decision arguments_json must contain an object")
                    if decision["tool"] == "finish" and arguments:
                        raise ValueError("Finish accepts no arguments")
                    result = dict(id="chatcmpl-" + uuid.uuid4().hex, object="chat.completion", created=int(time.time()),
                        model=self.name, choices=[dict(index=0, finish_reason="stop",
                            message=dict(role="assistant", content=json.dumps(decision, ensure_ascii=False)))],
                        usage=dict(prompt_tokens=prompt_tokens, completion_tokens=int(generated.shape[0]), total_tokens=int(output.shape[1])))
                except Exception as exc:
                    failure = (type(exc).__name__, str(exc))
                    exc.__traceback__ = None
                finally:
                    model = tokens = output = generated = None
                    gc.collect()
                    torch.cuda.empty_cache()
            if failure:
                if failure[0] == "RequestError":
                    raise RequestError(failure[1])
                raise RuntimeError(f"{failure[0]}: {failure[1]}")
            return result
        finally:
            self.semaphore.release()


def handler_for(runtime):
    class Handler(BaseHTTPRequestHandler):
        def respond(self, status, value):
            data = json.dumps(value, ensure_ascii=False).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            if self.path == "/health":
                return self.respond(200, runtime.identity())
            if self.path == "/v1/models":
                return self.respond(200, dict(object="list", data=[dict(id=runtime.name, object="model", owned_by="local")]))
            self.respond(404, dict(error="Not found"))

        def do_POST(self):
            if self.path != "/v1/chat/completions":
                return self.respond(404, dict(error="Not found"))
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if not 1 <= length <= 8_000_000:
                    raise RequestError("Invalid request size")
                body = json.loads(self.rfile.read(length))
                if not isinstance(body, dict):
                    raise RequestError("Expected a JSON object")
                result = runtime.infer(body)
                self.respond(200, result)
            except BlockingIOError as exc:
                self.respond(429, dict(error=dict(type="model_busy", message=str(exc))))
            except (RequestError, json.JSONDecodeError) as exc:
                self.respond(400, dict(error=dict(type="invalid_request", message=str(exc))))
            except TimeoutError as exc:
                self.respond(503, dict(error=dict(type="gpu_busy", message=str(exc))))
            except Exception as exc:
                # Malformed model output remains a failed proposal, never an invented action.
                self.respond(422, dict(error=dict(type="proposal_failed", message=str(exc))))
    return Handler


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", choices=["127.0.0.1", "::1"], default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8781)
    parser.add_argument("--adapter", type=Path)
    parser.add_argument("--max-context", type=int, default=1024)
    args = parser.parse_args()
    if args.host != "127.0.0.1":
        import socket
        ThreadingHTTPServer.address_family = socket.AF_INET6
    runtime = Runtime(adapter=args.adapter, max_context=args.max_context)
    server = ThreadingHTTPServer((args.host, args.port), handler_for(runtime))
    print(json.dumps(dict(listening=f"http://{args.host}:{args.port}", model=runtime.name)), flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
