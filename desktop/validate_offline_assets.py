from __future__ import annotations

import argparse
import ctypes
import gc
import hashlib
import json
import os
import subprocess
import tempfile
import wave
from pathlib import Path


def process_memory() -> dict[str, int]:
    if os.name != "nt":
        return {}

    from ctypes import wintypes

    class ProcessMemoryCounters(ctypes.Structure):
        _fields_ = [
            ("cb", wintypes.DWORD),
            ("PageFaultCount", wintypes.DWORD),
            ("PeakWorkingSetSize", ctypes.c_size_t),
            ("WorkingSetSize", ctypes.c_size_t),
            ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
            ("QuotaPagedPoolUsage", ctypes.c_size_t),
            ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
            ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
            ("PagefileUsage", ctypes.c_size_t),
            ("PeakPagefileUsage", ctypes.c_size_t),
            ("PrivateUsage", ctypes.c_size_t),
        ]

    counters = ProcessMemoryCounters()
    counters.cb = ctypes.sizeof(counters)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    psapi = ctypes.WinDLL("psapi", use_last_error=True)
    kernel32.GetCurrentProcess.restype = wintypes.HANDLE
    psapi.GetProcessMemoryInfo.argtypes = [
        wintypes.HANDLE,
        ctypes.POINTER(ProcessMemoryCounters),
        wintypes.DWORD,
    ]
    psapi.GetProcessMemoryInfo.restype = wintypes.BOOL
    process = kernel32.GetCurrentProcess()
    if not psapi.GetProcessMemoryInfo(
        process, ctypes.byref(counters), counters.cb
    ):
        raise ctypes.WinError(ctypes.get_last_error())
    return {
        "working_set": int(counters.WorkingSetSize),
        "peak_working_set": int(counters.PeakWorkingSetSize),
        "private_bytes": int(counters.PrivateUsage),
        "peak_pagefile_bytes": int(counters.PeakPagefileUsage),
    }


def md5_file(path: Path) -> str:
    digest = hashlib.md5(usedforsecurity=False)
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def validate_assets(root: Path, load_models: bool = True) -> dict:
    root = root.resolve()
    models = root / "models"
    embedding = models / "huggingface" / "embedding"
    reranker = models / "huggingface" / "reranker"
    whisper = models / "huggingface" / "whisper-small"
    easyocr = models / "easyocr"
    detector = easyocr / "model" / "craft_mlt_25k.pth"
    recognizer = easyocr / "model" / "cyrillic_g2.pth"
    soffice = root / "runtime" / "libreoffice" / "program" / "soffice.com"
    required = {
        "embedding": embedding / "model.safetensors",
        "reranker": reranker / "model.safetensors",
        "whisper": whisper / "model.bin",
        "easyocr_detector": detector,
        "easyocr_recognizer": recognizer,
        "libreoffice": soffice,
    }
    missing = {name: str(path) for name, path in required.items() if not path.is_file()}
    if missing:
        raise RuntimeError(f"Missing offline assets: {missing}")
    if md5_file(detector) != "2f8227d2def4037cdb3b34389dcf9ec1":
        raise RuntimeError("EasyOCR detector checksum mismatch")
    if md5_file(recognizer) != "19f85f43d9128a89ac21b8d6a06973fe":
        raise RuntimeError("EasyOCR recognizer checksum mismatch")

    with tempfile.TemporaryDirectory(prefix="atlas-lo-profile-") as profile:
        profile_uri = Path(profile).resolve().as_uri()
        version = subprocess.run(
            [
                str(soffice),
                f"-env:UserInstallation={profile_uri}",
                "--headless",
                "--nologo",
                "--nodefault",
                "--nofirststartwizard",
                "--nolockcheck",
                "--version",
            ],
            capture_output=True,
            text=True,
            timeout=60,
            check=True,
        ).stdout.strip()
    checks: dict[str, object] = {"libreoffice_version": version}
    if load_models:
        os.environ.update(
            {
                "HF_HUB_OFFLINE": "1",
                "TRANSFORMERS_OFFLINE": "1",
                "EASYOCR_MODULE_PATH": str(easyocr),
            }
        )
        from sentence_transformers import CrossEncoder, SentenceTransformer

        memory = {"baseline": process_memory()}
        model = SentenceTransformer(str(embedding), local_files_only=True)
        checks["embedding_dimensions"] = int(model.encode(["query: Atlas"]).shape[1])
        memory["embedding_loaded"] = process_memory()
        del model
        gc.collect()
        memory["embedding_released"] = process_memory()

        model = CrossEncoder(str(reranker), local_files_only=True)
        checks["reranker_score"] = float(model.predict([("Atlas", "Atlas работает")])[0])
        memory["reranker_loaded"] = process_memory()
        del model
        gc.collect()
        memory["reranker_released"] = process_memory()

        from faster_whisper import WhisperModel

        model = WhisperModel(
            str(whisper), device="cpu", compute_type="int8", local_files_only=True
        )
        with tempfile.TemporaryDirectory(prefix="atlas-whisper-probe-") as folder:
            audio = Path(folder) / "silence.wav"
            with wave.open(str(audio), "wb") as output:
                output.setnchannels(1)
                output.setsampwidth(2)
                output.setframerate(16000)
                output.writeframes(b"\x00\x00" * 16000)
            segments, _ = model.transcribe(str(audio), language="ru", beam_size=1)
            checks["whisper_segments"] = len(list(segments))
        memory["whisper_loaded"] = process_memory()
        del model
        gc.collect()
        memory["whisper_released"] = process_memory()

        import easyocr as easyocr_module

        reader = easyocr_module.Reader(
            ["ru", "en"], gpu=False, verbose=False, download_enabled=False
        )
        import cv2
        import numpy

        image = numpy.full((120, 480, 3), 255, dtype=numpy.uint8)
        cv2.putText(image, "ATLAS 123", (20, 80), cv2.FONT_HERSHEY_SIMPLEX, 1.5, (0, 0, 0), 3)
        checks["easyocr_results"] = len(reader.readtext(image, detail=0))
        memory["easyocr_loaded"] = process_memory()
        del reader
        gc.collect()
        memory["easyocr_released"] = process_memory()
        checks["memory"] = memory
    return {"ready": True, "files": {name: str(path) for name, path in required.items()}, **checks}


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate Atlas offline Desktop assets")
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--skip-model-load", action="store_true")
    args = parser.parse_args()
    try:
        result = validate_assets(args.root, load_models=not args.skip_model_load)
    except Exception as exc:
        print(json.dumps({"ready": False, "error": str(exc)}, ensure_ascii=False, indent=2))
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
