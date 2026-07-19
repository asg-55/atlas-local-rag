from __future__ import annotations

import hashlib
import re

from .models import TextBlock


SEPARATORS = ("\n\n", "\n", ". ", "; ", ", ", " ")


def normalize_text(text: str) -> str:
    text = text.replace("\x00", " ").replace("\r\n", "\n")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def split_text(text: str, max_chars: int = 1200, overlap: int = 160) -> list[str]:
    text = normalize_text(text)
    if len(text) <= max_chars:
        return [text] if text else []
    chunks: list[str] = []
    start = 0
    while start < len(text):
        target_end = min(start + max_chars, len(text))
        end = target_end
        if target_end < len(text):
            floor = start + max_chars // 2
            best = -1
            for separator in SEPARATORS:
                position = text.rfind(separator, floor, target_end)
                best = max(best, position + len(separator) if position >= 0 else -1)
            if best > floor:
                end = best
        chunk = text[start:end].strip()
        if chunk:
            chunks.append(chunk)
        if end >= len(text):
            break
        start = max(start + 1, end - overlap)
    return chunks


def make_chunks(blocks: list[TextBlock], max_chars: int = 1200, overlap: int = 160) -> list[dict]:
    result: list[dict] = []
    seen: set[str] = set()
    for block in blocks:
        for part in split_text(block.text, max_chars=max_chars, overlap=overlap):
            normalized = normalize_text(part)
            digest = hashlib.sha256(normalized.casefold().encode("utf-8")).hexdigest()
            if digest in seen:
                continue
            seen.add(digest)
            result.append(
                {
                    "content": normalized,
                    "content_hash": digest,
                    "location": block.location,
                    "chunk_index": len(result),
                }
            )
    return result
