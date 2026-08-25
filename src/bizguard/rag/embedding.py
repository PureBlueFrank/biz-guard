"""Cached embedding client shared by retrieval evaluation and production search.

Embedding evidence may improve retrieval ranking, but deterministic policy
decisions must never depend on the provider being available.
"""

from __future__ import annotations

from hashlib import sha256
import json
import math
import os
from pathlib import Path
import time
import tempfile
from typing import Protocol

import httpx
import numpy as np

from pydantic import BaseModel

from bizguard.rag.injector import KnowledgeDocument


EMBEDDING_MODEL = "embedding-3"
CACHE_VERSION = "zhipu-embedding-3-v1"
ZHIPU_EMBEDDING_URL = "https://open.bigmodel.cn/api/paas/v4/embeddings"
DEFAULT_ZHIPU_AUTH_PATH = Path("~/.local/share/opencode/auth.json").expanduser()
MAX_BATCH_SIZE = 64


class EmbeddingError(ValueError):
    """Report provider, response, or cache failures without exposing credentials."""


class EmbeddingEvalResult(BaseModel):
    """Reproducible metadata and Recall@k for an embedding experiment."""

    model: str
    cache_version: str
    recall_at_k: dict[int, float]


class TextEmbedder(Protocol):
    """Small seam that lets tests evaluate retrieval without network access."""

    model: str
    cache_version: str

    def embed(self, texts: list[str]) -> list[list[float]]:
        """Return one vector per input text in order."""


class ZhipuEmbeddingClient:
    """Cached synchronous client for Zhipu ``embedding-3`` requests."""

    model = EMBEDDING_MODEL
    cache_version = CACHE_VERSION

    def __init__(
        self,
        api_key: str,
        cache_dir: Path,
        timeout_seconds: float = 20.0,
        dimensions: int = 2048,
        max_attempts: int = 3,
    ) -> None:
        if not api_key:
            raise EmbeddingError("Zhipu API key is required")
        if not 256 <= dimensions <= 2048:
            raise EmbeddingError("embedding dimensions must be between 256 and 2048")
        if timeout_seconds <= 0:
            raise EmbeddingError("embedding timeout must be positive")
        if max_attempts < 1:
            raise EmbeddingError("embedding max_attempts must be positive")
        self._api_key = api_key
        self._cache_dir = cache_dir
        self._timeout_seconds = timeout_seconds
        self._dimensions = dimensions
        self._max_attempts = max_attempts

    def embed(self, texts: list[str]) -> list[list[float]]:
        """Embed texts, using a content-addressed cache for offline replay."""
        if not texts:
            return []
        vectors: list[list[float]] = []
        for offset in range(0, len(texts), MAX_BATCH_SIZE):
            batch = texts[offset : offset + MAX_BATCH_SIZE]
            vectors.extend(self._embed_batch(batch))
        return vectors

    def _embed_batch(self, texts: list[str]) -> list[list[float]]:
        cache_path = self._cache_path(texts)
        if cache_path.is_file():
            return self._load_cache(cache_path, texts)
        response: httpx.Response | None = None
        for attempt in range(self._max_attempts):
            try:
                response = self._post(texts)
            except (httpx.TimeoutException, httpx.NetworkError):
                if attempt + 1 >= self._max_attempts:
                    raise
            else:
                if response.status_code != 429 and response.status_code < 500:
                    break
                if attempt + 1 >= self._max_attempts:
                    response.raise_for_status()
            time.sleep(min(2**attempt, 4))
        if response is None:  # pragma: no cover - retry loop either returns or raises
            raise RuntimeError("embedding request produced no response")
        response.raise_for_status()
        try:
            payload = response.json()
        except ValueError as exc:
            raise EmbeddingError("Zhipu embedding response is not valid JSON") from exc
        records = payload.get("data") if isinstance(payload, dict) else None
        if not isinstance(records, list) or len(records) != len(texts):
            raise EmbeddingError("Zhipu embedding response has unexpected data length")
        if not all(
            isinstance(record, dict) and isinstance(record.get("index"), int)
            for record in records
        ):
            raise EmbeddingError("Zhipu embedding response contains no vector indexes")
        records = sorted(records, key=lambda record: int(record["index"]))
        if [record["index"] for record in records] != list(range(len(texts))):
            raise EmbeddingError("Zhipu embedding response contains invalid vector indexes")
        normalized: list[list[float]] = []
        for record in records:
            vector = record.get("embedding") if isinstance(record, dict) else None
            if not isinstance(vector, list):
                raise EmbeddingError("Zhipu embedding response contains no vectors")
            try:
                normalized.append([float(value) for value in vector])
            except (TypeError, ValueError) as exc:
                raise EmbeddingError("Zhipu embedding response contains invalid vectors") from exc
        if any(
            len(vector) != self._dimensions or not all(math.isfinite(value) for value in vector)
            for vector in normalized
        ):
            raise EmbeddingError("Zhipu embedding response contains invalid vector dimensions")
        self._write_cache(cache_path, texts, normalized)
        return normalized

    def _post(self, texts: list[str]) -> httpx.Response:
        return httpx.post(
            ZHIPU_EMBEDDING_URL,
            headers={"Authorization": f"Bearer {self._api_key}"},
            json={"model": self.model, "input": texts, "dimensions": self._dimensions},
            timeout=self._timeout_seconds,
        )

    def _cache_path(self, texts: list[str]) -> Path:
        fingerprint = sha256(
            json.dumps(
                {
                    "cache_version": self.cache_version,
                    "dimensions": self._dimensions,
                    "model": self.model,
                    "texts": texts,
                },
                ensure_ascii=False,
                sort_keys=True,
            ).encode("utf-8")
        ).hexdigest()
        return self._cache_dir / f"{fingerprint}.json"

    def _load_cache(self, path: Path, texts: list[str]) -> list[list[float]]:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, ValueError) as exc:
            raise EmbeddingError("embedding cache is unreadable or invalid") from exc
        if (
            payload.get("cache_version") != self.cache_version
            or payload.get("dimensions") != self._dimensions
            or payload.get("model") != self.model
            or payload.get("texts") != texts
            or not isinstance(payload.get("vectors"), list)
        ):
            raise EmbeddingError(f"embedding cache validation failed: {path}")
        try:
            vectors = [[float(value) for value in vector] for vector in payload["vectors"]]
        except (TypeError, ValueError) as exc:
            raise EmbeddingError("embedding cache contains invalid vectors") from exc
        if any(
            len(vector) != self._dimensions or not all(math.isfinite(value) for value in vector)
            for vector in vectors
        ):
            raise EmbeddingError(f"embedding cache validation failed: {path}")
        return vectors

    def _write_cache(self, path: Path, texts: list[str], vectors: list[list[float]]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        serialized = json.dumps(
            {
                "cache_version": self.cache_version,
                "dimensions": self._dimensions,
                "model": self.model,
                "texts": texts,
                "vectors": vectors,
            },
            ensure_ascii=False,
            sort_keys=True,
        )
        with tempfile.NamedTemporaryFile(
            "w", encoding="utf-8", dir=path.parent, delete=False
        ) as stream:
            temporary = Path(stream.name)
            stream.write(serialized)
        try:
            os.chmod(temporary, 0o600)
            temporary.replace(path)
        finally:
            temporary.unlink(missing_ok=True)


def load_zhipu_api_key(auth_path: Path = DEFAULT_ZHIPU_AUTH_PATH) -> str | None:
    """Read the locally configured Zhipu key without persisting it in the repository."""
    if not auth_path.is_file():
        return None
    payload = json.loads(auth_path.read_text(encoding="utf-8"))
    provider = payload.get("zhipuai-coding-plan") if isinstance(payload, dict) else None
    key = provider.get("key") if isinstance(provider, dict) else None
    return key if isinstance(key, str) and key else None


def split_document(document: KnowledgeDocument, max_characters: int = 800) -> list[str]:
    """Split a document into stable character chunks for embedding evaluation."""
    text = document.content.strip()
    if not text:
        return []
    return [text[index : index + max_characters] for index in range(0, len(text), max_characters)]


def retrieve_document_ids(
    query: str, documents: list[KnowledgeDocument], embedder: TextEmbedder, limit: int
) -> list[str]:
    """Rank document IDs by their best chunk cosine similarity to ``query``."""
    if limit < 1:
        raise ValueError("limit must be positive")
    chunk_records = [
        (document.id, chunk) for document in documents for chunk in split_document(document)
    ]
    if not chunk_records:
        return []
    query_vectors = embedder.embed([query])
    chunk_vectors_raw = embedder.embed([chunk for _, chunk in chunk_records])
    if len(query_vectors) != 1 or len(chunk_vectors_raw) != len(chunk_records):
        raise ValueError("embedder returned a vector count different from its inputs")
    query_vector = np.asarray(query_vectors[0], dtype=float)
    chunk_vectors = np.asarray(chunk_vectors_raw, dtype=float)
    if (
        query_vector.ndim != 1
        or chunk_vectors.ndim != 2
        or chunk_vectors.shape[1] != query_vector.size
    ):
        raise ValueError("embedding vectors must have matching non-empty dimensions")
    query_norm = np.linalg.norm(query_vector)
    chunk_norms = np.linalg.norm(chunk_vectors, axis=1)
    if query_norm == 0 or np.any(chunk_norms == 0):
        raise ValueError("embedding vectors must not be zero")
    similarities = (chunk_vectors @ query_vector) / (chunk_norms * query_norm)
    scores: dict[str, float] = {}
    for (document_id, _), score in zip(chunk_records, similarities, strict=True):
        scores[document_id] = max(scores.get(document_id, float("-inf")), float(score))
    return [
        document_id
        for document_id, _ in sorted(scores.items(), key=lambda item: (-item[1], item[0]))[:limit]
    ]
