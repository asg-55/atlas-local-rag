from __future__ import annotations

import gc
import os
from functools import lru_cache

import numpy as np
from sentence_transformers import CrossEncoder, SentenceTransformer


@lru_cache(maxsize=2)
def embedding_model(model_name: str) -> SentenceTransformer:
    return SentenceTransformer(model_name)


@lru_cache(maxsize=2)
def cross_encoder(model_name: str) -> CrossEncoder:
    return CrossEncoder(model_name)


def low_memory_mode() -> bool:
    return os.getenv("ATLAS_LOW_MEMORY", "0").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def release_embedding_models() -> None:
    embedding_model.cache_clear()
    cross_encoder.cache_clear()
    gc.collect()


def embed_passages(texts: list[str], model_name: str) -> np.ndarray:
    prepared = [f"passage: {text}" for text in texts]
    try:
        return embedding_model(model_name).encode(
            prepared,
            batch_size=32,
            show_progress_bar=False,
            normalize_embeddings=True,
            convert_to_numpy=True,
        ).astype("float32")
    finally:
        if low_memory_mode():
            release_embedding_models()


def embed_query(text: str, model_name: str) -> np.ndarray:
    try:
        return embedding_model(model_name).encode(
            [f"query: {text}"],
            show_progress_bar=False,
            normalize_embeddings=True,
            convert_to_numpy=True,
        ).astype("float32")[0]
    finally:
        if low_memory_mode():
            release_embedding_models()
