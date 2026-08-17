from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import time
from pathlib import Path

from huggingface_hub import snapshot_download


def safe_target(root: Path, relative: str) -> Path:
    target = (root / relative).resolve()
    try:
        target.relative_to(root.resolve())
    except ValueError as exc:
        raise ValueError(f"Model destination escapes staging: {relative}") from exc
    return target


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def download_snapshot(model: dict, cache_dir: Path) -> Path:
    last_error: Exception | None = None
    for attempt in range(1, 4):
        try:
            return Path(
                snapshot_download(
                    repo_id=model["repository"],
                    revision=model["revision"],
                    allow_patterns=model["allow_patterns"],
                    cache_dir=cache_dir,
                    max_workers=1,
                )
            )
        except Exception as exc:
            last_error = exc
            if attempt < 3:
                time.sleep(attempt * 2)
    raise RuntimeError(f"Failed to download {model['id']} after 3 attempts") from last_error


def prepare_models(manifest_path: Path, destination: Path, cache_dir: Path | None = None) -> dict:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("schema_version") != 1:
        raise ValueError("Unsupported model-pack manifest")
    destination = destination.resolve()
    destination.mkdir(parents=True, exist_ok=True)
    cache_dir = (cache_dir or destination.parent / ".download-cache").resolve()
    cache_dir.mkdir(parents=True, exist_ok=True)
    prepared = []
    for model in manifest.get("models", []):
        target = safe_target(destination, model["destination"])
        snapshot = download_snapshot(model, cache_dir)
        if target.exists():
            shutil.rmtree(target)
        target.mkdir(parents=True)
        for relative in model["allow_patterns"]:
            source = snapshot / relative
            output = target / relative
            if not source.is_file():
                raise RuntimeError(f"Missing cached file for {model['id']}: {relative}")
            output.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, output)
        missing = [pattern for pattern in model["allow_patterns"] if not (target / pattern).is_file()]
        if missing:
            raise RuntimeError(f"Missing files for {model['id']}: {', '.join(missing)}")
        files = [path for path in target.rglob("*") if path.is_file()]
        prepared.append(
            {
                "id": model["id"],
                "repository": model["repository"],
                "revision": model["revision"],
                "destination": str(target),
                "files": [
                    {
                        "path": path.relative_to(target).as_posix(),
                        "bytes": path.stat().st_size,
                        "sha256": sha256_file(path),
                    }
                    for path in sorted(files)
                ],
                "bytes": sum(path.stat().st_size for path in files),
            }
        )
    if not prepared:
        raise ValueError("Model-pack manifest is empty")
    return {"ready": True, "models": prepared}


def main() -> int:
    parser = argparse.ArgumentParser(description="Prepare pinned offline model packs")
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--destination", type=Path, required=True)
    parser.add_argument("--cache-dir", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = prepare_models(args.manifest, args.destination, args.cache_dir)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps({"ready": True, "models": len(result["models"])}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
