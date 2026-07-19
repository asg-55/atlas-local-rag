from __future__ import annotations

import math
import re
from collections import defaultdict

import numpy as np
from rank_bm25 import BM25Okapi

from .config import Settings
from .database import Database
from .embeddings import cross_encoder, embed_query
from .models import SearchResult
from .vector_index import VectorIndex


TOKEN_PATTERN = re.compile(r"[\w№.-]+", re.UNICODE)


def tokenize(text: str) -> list[str]:
    return [token.casefold() for token in TOKEN_PATTERN.findall(text) if len(token) > 1]


class HybridRetriever:
    def __init__(self, db: Database, index: VectorIndex, settings: Settings):
        self.db = db
        self.index = index
        self.settings = settings
        self._lexical_generation = -1
        self._lexical_ids: list[int] = []
        self._bm25: BM25Okapi | None = None

    def _ensure_lexical(self) -> None:
        generation = self.db.generation()
        if self._lexical_generation == generation:
            return
        rows = self.db.all_chunks()
        self._lexical_ids = [int(row["id"]) for row in rows]
        corpus = [tokenize(row["content"]) for row in rows]
        self._bm25 = BM25Okapi(corpus) if corpus else None
        self._lexical_generation = generation

    def _lexical_search(self, query: str, k: int, allowed_ids: set[int] | None = None) -> list[tuple[int, float]]:
        self._ensure_lexical()
        if not self._bm25 or not self._lexical_ids:
            return []
        scores = np.asarray(self._bm25.get_scores(tokenize(query)), dtype="float32")
        if not np.any(scores > 0):
            return []
        eligible = np.arange(len(scores))
        if allowed_ids is not None:
            eligible = np.asarray(
                [pos for pos, chunk_id in enumerate(self._lexical_ids) if chunk_id in allowed_ids],
                dtype="int64",
            )
        if len(eligible) == 0:
            return []
        count = min(k, len(eligible))
        eligible_scores = scores[eligible]
        local_positions = np.argpartition(eligible_scores, -count)[-count:]
        positions = eligible[local_positions]
        positions = positions[np.argsort(scores[positions])[::-1]]
        return [(self._lexical_ids[int(pos)], float(scores[pos])) for pos in positions if scores[pos] > 0]

    def search(
        self,
        query: str,
        final_k: int | None = None,
        document_id: str | None = None,
        include_all: bool = False,
    ) -> list[SearchResult]:
        final_k = final_k or self.settings.final_chunks
        allowed_ids = None
        if document_id:
            document_chunks = self.db.chunks_for_document(document_id)
            if include_all:
                return [SearchResult(chunk=chunk, score=1.0) for chunk in document_chunks[:final_k]]
            allowed_ids = {chunk.id for chunk in document_chunks}
        vector = embed_query(query, self.settings.embedding_model)
        dense_k = max(self.settings.dense_candidates, 160) if allowed_ids is not None else self.settings.dense_candidates
        dense = self.index.search(vector, dense_k)
        if allowed_ids is not None:
            dense = [(chunk_id, score) for chunk_id, score in dense if chunk_id in allowed_ids]
        lexical_k = max(self.settings.lexical_candidates, 160) if allowed_ids is not None else self.settings.lexical_candidates
        lexical = self._lexical_search(query, lexical_k, allowed_ids=allowed_ids)
        fused: dict[int, float] = defaultdict(float)
        dense_scores = dict(dense)
        lexical_scores = dict(lexical)
        for rank, (chunk_id, _) in enumerate(dense, start=1):
            fused[chunk_id] += 1.0 / (60 + rank)
        for rank, (chunk_id, _) in enumerate(lexical, start=1):
            fused[chunk_id] += 1.0 / (60 + rank)
        candidate_ids = [item[0] for item in sorted(fused.items(), key=lambda item: item[1], reverse=True)[:50]]
        chunks = self.db.chunks_by_ids(candidate_ids)
        candidates = []
        seen_content: set[str] = set()
        for chunk_id in candidate_ids:
            if chunk_id not in chunks:
                continue
            fingerprint = chunks[chunk_id].content.casefold().strip()
            if fingerprint in seen_content:
                continue
            seen_content.add(fingerprint)
            candidates.append(
                SearchResult(
                    chunk=chunks[chunk_id],
                    score=fused[chunk_id],
                    dense_score=dense_scores.get(chunk_id, 0.0),
                    lexical_score=lexical_scores.get(chunk_id, 0.0),
                )
            )
        if self.settings.enable_reranker and candidates:
            model = cross_encoder(self.settings.reranker_model)
            logits = model.predict([(query, result.chunk.content) for result in candidates], show_progress_bar=False)
            max_fused = max(result.score for result in candidates) or 1.0
            for result, logit in zip(candidates, logits):
                rerank = 1.0 / (1.0 + math.exp(-float(np.clip(logit, -30, 30))))
                result.reranker_score = rerank
                result.score = 0.85 * rerank + 0.15 * (result.score / max_fused)
        else:
            max_fused = max((result.score for result in candidates), default=1.0)
            for result in candidates:
                result.score /= max_fused
        candidates.sort(key=lambda item: item.score, reverse=True)
        filtered = [item for item in candidates if item.score >= self.settings.min_relevance]
        return filtered[:final_k]
