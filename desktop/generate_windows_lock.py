from __future__ import annotations

import argparse
import json
from pathlib import Path
from urllib.parse import unquote, urlparse


def build_lock(report: dict, overrides: dict | None = None) -> dict:
    overrides = overrides or {}
    packages = []
    seen: set[str] = set()
    for item in report.get("install", []):
        metadata = item.get("metadata") or {}
        download = item.get("download_info") or {}
        url = str(download.get("url") or "")
        sha256 = str((download.get("archive_info") or {}).get("hashes", {}).get("sha256") or "")
        name = str(metadata.get("name") or "").strip()
        version = str(metadata.get("version") or "").strip()
        filename = Path(unquote(urlparse(url).path)).name
        override = overrides.get(name) or {}
        if override:
            url = str(override.get("url") or "")
            sha256 = str(override.get("sha256") or "")
            filename = str(override.get("filename") or "")
        normalized = name.lower().replace("_", "-")
        if not name or not version or not url or len(sha256) != 64:
            raise ValueError(f"Неполная запись pip report: {name or url}")
        if not filename.endswith(".whl"):
            raise ValueError(f"Desktop lock допускает только wheel: {filename}")
        if normalized in seen:
            raise ValueError(f"Повторяющийся пакет: {name}")
        seen.add(normalized)
        packages.append(
            {
                "name": name,
                "version": version,
                "filename": filename,
                "url": url,
                "sha256": sha256,
                "requested": bool(item.get("requested")),
            }
        )
    if not packages:
        raise ValueError("pip report не содержит установочных записей")
    packages.sort(key=lambda package: package["name"].lower())
    return {
        "schema_version": 1,
        "python": "3.11",
        "platform": "win_amd64",
        "only_binary": True,
        "packages": packages,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Создание wheel lock Atlas Desktop")
    parser.add_argument("report", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--overrides", type=Path)
    args = parser.parse_args()
    report = json.loads(args.report.read_text(encoding="utf-8"))
    overrides = (
        json.loads(args.overrides.read_text(encoding="utf-8"))
        if args.overrides
        else {}
    )
    lock = build_lock(report, overrides)
    args.output.write_text(
        json.dumps(lock, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(f"Locked packages: {len(lock['packages'])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
