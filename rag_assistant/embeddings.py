from __future__ import annotations

from functools import lru_cache

import numpy as np
from sentence_transformers import CrossEncoder, SentenceTransformer


@lru_cache(maxsize=2)
def embedding_model(model_name: str) -> SentenceTransformer:
    return SentenceTransformer(model_name)


@lru_cache(maxsize=2)
def cross_encoder(model_name: str) -> CrossEncoder:
    return CrossEncoder(model_name)


def embed_passages(texts: list[str], model_name: str) -> np.ndarray:
    prepared = [f"passage: {text}" for text in texts]
    return embedding_model(model_name).encode(
        prepared, batch_size=32, show_progress_bar=False, normalize_embeddings=True, convert_to_numpy=True
    ).astype("float32")


def embed_query(text: str, model_name: str) -> np.ndarray:
    return embedding_model(model_name).encode(
        [f"query: {text}"], show_progress_bar=False, normalize_embeddings=True, convert_to_numpy=True
    ).astype("float32")[0]
