from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
import urllib.request
import zipfile
from pathlib import Path


DEFAULT_MANIFEST = Path(__file__).with_name("components.json")


def load_manifest(path: Path) -> dict:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("schema_version") != 1:
        raise ValueError("Неподдерживаемая версия manifest")
    components = payload.get("components")
    if not isinstance(components, list) or not components:
        raise ValueError("Manifest не содержит компонентов")
    required = {"id", "kind", "filename", "url", "sha256", "size", "destination"}
    seen: set[str] = set()
    for component in components:
        missing = required.difference(component)
        if missing:
            raise ValueError(f"Компонент неполон: {', '.join(sorted(missing))}")
        component_id = component["id"]
        if component_id in seen:
            raise ValueError(f"Повторяющийся id компонента: {component_id}")
        seen.add(component_id)
        if component["kind"] not in {"file", "zip", "msi"}:
            raise ValueError(f"Неизвестный тип компонента: {component['kind']}")
        if len(component["sha256"]) != 64:
            raise ValueError(f"Некорректный SHA-256: {component_id}")
    return payload


def sha256(path: Path, chunk_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(chunk_size), b""):
            digest.update(chunk)
    return digest.hexdigest()


def verify_download(path: Path, component: dict) -> None:
    actual_size = path.stat().st_size
    if actual_size != component["size"]:
        raise ValueError(
            f"Неверный размер {component['id']}: {actual_size}, ожидалось {component['size']}"
        )
    actual_hash = sha256(path)
    if actual_hash.lower() != component["sha256"].lower():
        raise ValueError(f"SHA-256 не совпал для {component['id']}")


def safe_extract(archive: Path, destination: Path) -> None:
    destination = destination.resolve()
    destination.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(archive) as package:
        for member in package.infolist():
            target = (destination / member.filename).resolve()
            if target != destination and destination not in target.parents:
                raise ValueError(f"Небезопасный путь в архиве: {member.filename}")
        package.extractall(destination)


def download(component: dict, downloads_dir: Path) -> Path:
    downloads_dir.mkdir(parents=True, exist_ok=True)
    target = downloads_dir / component["filename"]
    if target.is_file():
        try:
            verify_download(target, component)
            return target
        except ValueError:
            target.unlink()
    partial = target.with_suffix(target.suffix + ".part")
    request = urllib.request.Request(component["url"], headers={"User-Agent": "Atlas-Desktop-Builder/1"})
    try:
        with urllib.request.urlopen(request, timeout=60) as response, partial.open("wb") as output:
            shutil.copyfileobj(response, output, length=1024 * 1024)
        verify_download(partial, component)
        partial.replace(target)
        return target
    except Exception:
        partial.unlink(missing_ok=True)
        raise


def install_component(component: dict, archive: Path, destination_root: Path) -> Path:
    destination = (destination_root / component["destination"]).resolve()
    root = destination_root.resolve()
    if destination != root and root not in destination.parents:
        raise ValueError(f"Destination вне staging: {component['destination']}")
    if component["kind"] == "msi":
        raise ValueError("MSI-компонент устанавливает desktop/build_offline_assets.ps1")
    if component["kind"] == "zip":
        safe_extract(archive, destination)
    else:
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(archive, destination)
    return destination


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Подготовка изолированного runtime Atlas Desktop")
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--destination", type=Path, default=Path("desktop/staging"))
    parser.add_argument("--component", action="append", dest="components")
    parser.add_argument("--list", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    manifest = load_manifest(args.manifest)
    available = {item["id"]: item for item in manifest["components"]}
    if args.list:
        for component in available.values():
            print(f"{component['id']}: {component['filename']} ({component['size']} bytes)")
        return 0
    selected = args.components or [
        component_id
        for component_id, component in available.items()
        if component["kind"] != "msi"
    ]
    unknown = sorted(set(selected).difference(available))
    if unknown:
        print(f"Неизвестные компоненты: {', '.join(unknown)}", file=sys.stderr)
        return 2
    downloads_dir = args.destination / "downloads"
    for component_id in selected:
        component = available[component_id]
        print(f"Подготовка {component_id}...")
        artifact = download(component, downloads_dir)
        installed = install_component(component, artifact, args.destination)
        print(f"Готово: {installed}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
