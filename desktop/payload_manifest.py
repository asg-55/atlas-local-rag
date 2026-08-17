from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath


MANIFEST_NAME = "payload-manifest.json"
FORBIDDEN_ROOTS = {"downloads", "validation"}


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def payload_files(root: Path) -> list[Path]:
    root = root.resolve()
    files: list[Path] = []
    for path in root.rglob("*"):
        relative = path.relative_to(root)
        if relative.parts and relative.parts[0].casefold() in FORBIDDEN_ROOTS:
            raise ValueError(f"Запрещённый каталог в payload: {relative.parts[0]}")
        if path.is_symlink():
            raise ValueError(f"Ссылки запрещены в установочном payload: {relative}")
        if path.is_file() and relative.as_posix() != MANIFEST_NAME:
            files.append(path)
    return sorted(files, key=lambda item: item.relative_to(root).as_posix().casefold())


def create_manifest(root: Path, output: Path, source_commit: str) -> dict:
    root = root.resolve()
    output = output.resolve()
    if output.parent != root or output.name != MANIFEST_NAME:
        raise ValueError(f"Manifest должен находиться в корне payload: {root / MANIFEST_NAME}")
    entries = []
    total_bytes = 0
    for path in payload_files(root):
        size = path.stat().st_size
        total_bytes += size
        entries.append(
            {
                "path": path.relative_to(root).as_posix(),
                "size": size,
                "sha256": file_sha256(path),
            }
        )
    payload = {
        "schema_version": 1,
        "source_commit": source_commit,
        "file_count": len(entries),
        "total_bytes": total_bytes,
        "files": entries,
    }
    temporary = output.with_suffix(".json.tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    temporary.replace(output)
    return payload


def safe_manifest_path(root: Path, value: str) -> Path:
    relative = PurePosixPath(value)
    if relative.is_absolute() or not relative.parts or ".." in relative.parts:
        raise ValueError(f"Небезопасный путь в manifest: {value}")
    path = root.joinpath(*relative.parts).resolve()
    if root not in path.parents:
        raise ValueError(f"Путь выходит за payload: {value}")
    return path


def verify_manifest(root: Path, manifest_path: Path) -> dict:
    root = root.resolve()
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    if payload.get("schema_version") != 1 or not isinstance(payload.get("files"), list):
        raise ValueError("Некорректный payload manifest")

    declared: dict[str, dict] = {}
    for entry in payload["files"]:
        relative = str(entry.get("path", ""))
        path = safe_manifest_path(root, relative)
        normalized = path.relative_to(root).as_posix()
        if normalized in declared:
            raise ValueError(f"Повтор пути в manifest: {normalized}")
        declared[normalized] = entry

    actual = {
        path.relative_to(root).as_posix(): path for path in payload_files(root)
    }
    if set(declared) != set(actual):
        missing = sorted(set(declared) - set(actual))
        extra = sorted(set(actual) - set(declared))
        raise ValueError(f"Состав payload изменён; отсутствуют={missing}, лишние={extra}")

    total_bytes = 0
    for relative, path in actual.items():
        entry = declared[relative]
        size = path.stat().st_size
        total_bytes += size
        if size != int(entry.get("size", -1)):
            raise ValueError(f"Размер не совпал: {relative}")
        if file_sha256(path) != str(entry.get("sha256", "")).casefold():
            raise ValueError(f"SHA-256 не совпал: {relative}")
    if payload.get("file_count") != len(actual) or payload.get("total_bytes") != total_bytes:
        raise ValueError("Итоговые значения payload manifest не совпали")
    return payload


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="SHA-256 manifest Atlas Desktop payload")
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--create", action="store_true")
    action.add_argument("--verify", action="store_true")
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--manifest", type=Path)
    parser.add_argument("--source-commit", default="unknown")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    root = args.root.resolve()
    manifest = (args.manifest or root / MANIFEST_NAME).resolve()
    payload = (
        create_manifest(root, manifest, args.source_commit)
        if args.create
        else verify_manifest(root, manifest)
    )
    print(json.dumps({key: payload[key] for key in ("source_commit", "file_count", "total_bytes")}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
