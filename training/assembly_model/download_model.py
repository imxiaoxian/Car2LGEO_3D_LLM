"""Download the pinned upstream model without running model-supplied code."""
import argparse
from pathlib import Path
import re
import time
from common import ROOT, MODEL_ID, REVISION, configure_cache, file_hash, write_json


def fresh_weight(folder, cache):
    """Bypass stale CDN redirects and resume only the content-addressed weight."""
    import requests
    resolve = f"https://huggingface.co/{MODEL_ID}/resolve/{REVISION}/model.safetensors"
    last_error = None
    for attempt in range(3):
        try:
            response = requests.head(resolve, params={"download": "true", "nonce": str(time.time_ns())},
                allow_redirects=False, timeout=(20, 30))
            if response.status_code not in {301, 302, 303, 307, 308}:
                raise RuntimeError(f"Expected a fresh upstream weight redirect: {response.status_code}")
            digest = response.headers.get("X-Linked-Etag", "").strip(chr(34))
            size = int(response.headers.get("X-Linked-Size", "0"))
            if not re.fullmatch(r"[0-9a-f]{64}", digest) or size < 1:
                raise RuntimeError("Upstream weight integrity metadata is missing")
            complete = folder / "model.safetensors"
            if complete.exists() and complete.stat().st_size == size and file_hash(complete) == digest:
                return
            partial = cache / ("models--" + MODEL_ID.replace("/", "--")) / "blobs" / (digest + ".incomplete")
            partial.parent.mkdir(parents=True, exist_ok=True)
            offset = partial.stat().st_size if partial.exists() else 0
            if offset > size:
                raise RuntimeError("Partial weight exceeds the upstream content size")
            if offset < size:
                headers = {"Range": f"bytes={offset}-"} if offset else {}
                with requests.get(response.headers["Location"], headers=headers, stream=True, timeout=(20, 120)) as weight:
                    if offset and (weight.status_code != 206 or not weight.headers.get("Content-Range", "").startswith(f"bytes {offset}-")):
                        raise RuntimeError("Upstream did not acknowledge the exact resume offset")
                    weight.raise_for_status()
                    print(f"Weight download attempt {attempt + 1}: resume {offset}/{size} bytes", flush=True)
                    with partial.open("ab" if offset else "wb") as stream:
                        for block in weight.iter_content(1024 * 1024):
                            if block:
                                stream.write(block)
            if partial.stat().st_size != size or file_hash(partial) != digest:
                raise RuntimeError("Downloaded weight failed size/SHA256 verification")
            partial.replace(complete)
            return
        except (requests.RequestException, RuntimeError) as exc:
            # Signed CDN URLs are intentionally omitted from diagnostic output.
            last_error = type(exc).__name__
            print(f"Weight download attempt {attempt + 1} failed: {last_error}", flush=True)
    raise RuntimeError(f"Pinned weight download failed after 3 attempts: {last_error}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--cache-dir", type=Path, default=ROOT / "cache/huggingface/hub")
    parser.add_argument("--fresh-weight-download", action="store_true")
    args = parser.parse_args()
    configure_cache()
    from huggingface_hub import snapshot_download
    patterns = ["*.json", "*.txt", "*.model", "*.tiktoken", "LICENSE", "README.md"]
    if not args.fresh_weight_download:
        patterns.append("*.safetensors")
    folder = Path(snapshot_download(MODEL_ID, revision=REVISION, cache_dir=str(args.cache_dir),
        allow_patterns=patterns, max_workers=4))
    if args.fresh_weight_download:
        fresh_weight(folder, args.cache_dir)
    files = {str(p.relative_to(folder)): file_hash(p) for p in folder.rglob("*") if p.is_file()}
    record = dict(schema="assembly-model-cache.v1", model_id=MODEL_ID, revision=REVISION, path=str(folder), files=files)
    write_json(ROOT / "runtime/model-cache.json", record)
    print(str(folder), flush=True)


if __name__ == "__main__":
    main()
