"""Download the two configured public revision-pinned safetensors snapshots, never an LLM.

This is a separate, disposable preparation process. The online worker is offline and
mounts the resulting cache read-only. No tokens, account files, or test secrets are mounted.
"""
from __future__ import annotations
import hashlib
import json
from pathlib import Path
import re

CONFIG = Path("/workspace/evaluation/retrieval-matrix.json")
PATTERNS = ["*.json", "*.txt", "*.model", "model.safetensors", "1_Pooling/*.json", "README.md"]


def manifest(config: dict, download) -> dict:
    result = {"schema_version": 1, "kind": "public-retrieval-models", "models": {}}
    for kind in ("embedding", "reranker"):
        repo, revision = config[kind], config["model_revisions"][kind]
        if not isinstance(repo, str) or not re.fullmatch(r"BAAI/[A-Za-z0-9._-]+", repo):
            raise ValueError("only configured public BAAI models are supported")
        if not isinstance(revision, str) or not re.fullmatch(r"[0-9a-f]{40}", revision):
            raise ValueError("model revision must be an immutable commit")
        path = Path(download(repo, revision=revision, allow_patterns=PATTERNS, token=False, max_workers=2))
        files = {}
        for file in sorted(path.rglob("*")):
            if file.is_file():
                with file.open("rb") as stream:
                    files[file.relative_to(path).as_posix()] = hashlib.file_digest(stream, "sha256").hexdigest()
        if "config.json" not in files or "model.safetensors" not in files or "tokenizer.json" not in files:
            raise ValueError("required safe model weights/tokenizer missing")
        if any(p.endswith((".bin", ".py", ".pkl")) for p in files):
            raise ValueError("unexpected executable or pickle model artifact")
        result["models"][kind] = {"repository": repo, "revision": revision, "files_sha256": files}
    return result


def main() -> None:
    from huggingface_hub import snapshot_download
    config = json.loads(CONFIG.read_text())
    result = manifest(config, snapshot_download)
    result["config_sha256"] = hashlib.sha256(CONFIG.read_bytes()).hexdigest()
    Path("/models/m13c-model-manifest.json").write_text(json.dumps(result, indent=2))
    print(json.dumps(result))


if __name__ == "__main__":
    main()
