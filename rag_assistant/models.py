from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class TextBlock:
    text: str
    location: str
    block_type: str = "text"
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class Chunk:
    id: int
    document_id: str
    filename: str
    content: str
    location: str
    chunk_index: int


@dataclass
class SearchResult:
    chunk: Chunk
    score: float
    dense_score: float = 0.0
    lexical_score: float = 0.0
    reranker_score: float | None = None

    def as_source(self) -> dict[str, Any]:
        return {
            "chunk_id": self.chunk.id,
            "document_id": self.chunk.document_id,
            "filename": self.chunk.filename,
            "location": self.chunk.location,
            "score": round(self.score, 4),
            "excerpt": self.chunk.content[:700],
        }
