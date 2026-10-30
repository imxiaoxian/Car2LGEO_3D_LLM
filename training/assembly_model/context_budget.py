"""Count complete website prompts with the pinned tokenizer; no GPU or training."""
import argparse
from hashlib import sha256
import json
from pathlib import Path

from common import MODEL_ID, REVISION, configure_cache, write_json


def main():
    parser = argparse.ArgumentParser()
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--cases", type=Path)
    source.add_argument("--payload", type=Path)
    parser.add_argument("--max-context", type=int, default=1024)
    parser.add_argument("--max-tokens", type=int, default=256)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if not 1 <= args.max_context <= 32768 or not 1 <= args.max_tokens <= 4096:
        parser.error("Expected context in1..32768 and output tokens in1..4096")
    if args.cases:
        cases = [json.loads(line) for line in args.cases.read_text(encoding="utf-8").splitlines() if line.strip()]
    else:
        payload = json.loads(args.payload.read_text(encoding="utf-8-sig"))
        if not isinstance(payload, dict):
            parser.error("Website payload must be an object")
        content = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        cases = [dict(id=args.payload.name, messages=[dict(role="user", content=content)],
                      input_sha256=sha256(content.encode()).hexdigest())]
    if not cases:
        parser.error("No saved website inputs to measure")
    configure_cache()
    from transformers import AutoTokenizer
    tokenizer = AutoTokenizer.from_pretrained(MODEL_ID, revision=REVISION, trust_remote_code=False, local_files_only=True)
    results = []
    for case in cases:
        messages = case["messages"]
        if len(messages) != 1 or messages[0].get("role") != "user" or not isinstance(messages[0].get("content"), str):
            raise ValueError("Expected the exact single website user message")
        digest = sha256(messages[0]["content"].encode()).hexdigest()
        if digest != case["input_sha256"]:
            raise ValueError("Saved website input hash mismatch")
        count = len(tokenizer.apply_chat_template(messages, tokenize=True, add_generation_prompt=True))
        total = count + args.max_tokens
        results.append(dict(id=case["id"], input_sha256=digest, input_tokens=count,
            requested_output_tokens=args.max_tokens, total_tokens=total, fits=total <= args.max_context))
    report = dict(schema="assembly-context-budget.v1", model_id=MODEL_ID, revision=REVISION,
        max_context=args.max_context, max_tokens=args.max_tokens, cases=results,
        all_fit=all(row["fits"] for row in results),
        scope="Tokenizer context fit only; no inference, training or assembly acceptance")
    write_json(args.output, report)
    print(json.dumps(report, ensure_ascii=False))
    if not report["all_fit"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
