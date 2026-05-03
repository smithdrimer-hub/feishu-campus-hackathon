"""Embedding provider interface for vector semantic search.

Follows the same pattern as llm_provider.py: Base interface + Fake (for tests) + OpenAI.
"""

from __future__ import annotations

import hashlib
import os
from typing import Any


class EmbeddingProvider:
    """Minimal interface for text embedding providers."""

    def embed(self, texts: list[str]) -> list[list[float]]:
        """Convert a list of texts into embedding vectors.

        Args:
            texts: List of strings to embed (batch).

        Returns:
            List of embedding vectors (each a list of floats).
        """
        raise NotImplementedError

    def embed_single(self, text: str) -> list[float]:
        """Convenience: embed a single text string."""
        return self.embed([text])[0]

    @property
    def dimension(self) -> int:
        """Return the embedding dimension for this provider."""
        raise NotImplementedError


class FakeEmbeddingProvider(EmbeddingProvider):
    """Deterministic provider for testing. Generates consistent pseudo-vectors from text content."""

    def __init__(self, dimension: int = 128) -> None:
        self._dimension = dimension

    @property
    def dimension(self) -> int:
        return self._dimension

    def embed(self, texts: list[str]) -> list[list[float]]:
        """Generate deterministic pseudo-embeddings based on text hash.

        Same text always produces the same vector. Similar texts will NOT have
        similar vectors — this is intentional for unit tests (deterministic, no API).
        """
        return [self._hash_to_vector(text) for text in texts]

    def _hash_to_vector(self, text: str) -> list[float]:
        digest = hashlib.sha256(text.encode("utf-8")).digest()
        raw = []
        while len(raw) < self._dimension:
            digest = hashlib.sha256(digest).digest()
            for byte in digest:
                raw.append((byte / 255.0) * 2 - 1)  # normalize to [-1, 1]
        return raw[: self._dimension]


class OpenAIEmbeddingProvider(EmbeddingProvider):
    """OpenAI-compatible embedding provider.

    Supports OpenAI text-embedding-3-small/large and any compatible endpoint.
    """

    def __init__(
        self,
        api_key: str | None = None,
        api_key_env: str = "OPENAI_API_KEY",
        base_url: str | None = None,
        model: str = "text-embedding-3-small",
    ) -> None:
        from openai import OpenAI

        resolved_key = api_key or os.environ.get(api_key_env, "")
        if not resolved_key:
            raise ValueError(
                f"Embedding API key not provided. Set {api_key_env} environment variable "
                f"or pass api_key to OpenAIEmbeddingProvider()."
            )
        client_kwargs: dict[str, Any] = {"api_key": resolved_key}
        if base_url is not None:
            client_kwargs["base_url"] = base_url
        self.client = OpenAI(**client_kwargs)
        self.model = model
        self._dimension = self._infer_dimension(model)

    @property
    def dimension(self) -> int:
        return self._dimension

    def embed(self, texts: list[str]) -> list[list[float]]:
        """Call OpenAI embeddings API in batch."""
        if not texts:
            return []
        cleaned = [t.replace("\n", " ").strip() or " " for t in texts]
        response = self.client.embeddings.create(model=self.model, input=cleaned)
        return [item.embedding for item in response.data]

    @staticmethod
    def _infer_dimension(model: str) -> int:
        known = {
            "text-embedding-3-small": 1536,
            "text-embedding-3-large": 3072,
            "text-embedding-ada-002": 1536,
        }
        return known.get(model, 1536)
