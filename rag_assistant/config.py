from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def _env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    return default if value is None else value.lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    data_dir: Path = Path(os.getenv("DATA_DIR", "data"))
    ollama_base_url: str = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
    chat_model: str = os.getenv("CHAT_MODEL", "qwen2.5:7b-instruct-q4_K_M")
    embedding_model: str = os.getenv("EMBEDDING_MODEL", "intfloat/multilingual-e5-base")
    reranker_model: str = os.getenv(
        "RERANKER_MODEL", "cross-encoder/mmarco-mMiniLMv2-L12-H384-v1"
    )
    enable_reranker: bool = _env_bool("ENABLE_RERANKER", True)
    dense_candidates: int = int(os.getenv("DENSE_CANDIDATES", "40"))
    lexical_candidates: int = int(os.getenv("LEXICAL_CANDIDATES", "40"))
    final_chunks: int = int(os.getenv("FINAL_CHUNKS", "7"))
    min_relevance: float = float(os.getenv("MIN_RELEVANCE", "0.08"))
    max_context_chars: int = int(os.getenv("MAX_CONTEXT_CHARS", "18000"))
    ocr_pdf_mode: str = os.getenv("OCR_PDF_MODE", "auto").lower()
    ocr_dpi: int = int(os.getenv("OCR_DPI", "220"))
    app_password: str = os.getenv("APP_PASSWORD", "")

    @property
    def db_path(self) -> Path:
        return self.data_dir / "assistant.db"

    @property
    def documents_dir(self) -> Path:
        return self.data_dir / "documents"

    @property
    def index_path(self) -> Path:
        return self.data_dir / "vector.index"

    @property
    def index_meta_path(self) -> Path:
        return self.data_dir / "vector.index.json"

    def ensure_directories(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.documents_dir.mkdir(parents=True, exist_ok=True)


settings = Settings()
