"""Vector store for semantic search over memory items.

Uses ChromaDB for persistent vector storage. Two collections:
- memories: indexes MemoryItem current_value + rationale
- evidence: indexes source_refs excerpts

Graceful degradation: if ChromaDB is unavailable, all methods return empty results.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from memory.embedding_provider import EmbeddingProvider
    from memory.schema import MemoryItem

logger = logging.getLogger(__name__)

DEFAULT_SIMILARITY_THRESHOLD = 0.35


class VectorStore:
    """ChromaDB-backed vector store for semantic memory search.

    Provides two collections:
    - memories: full memory text (current_value + rationale)
    - evidence: individual source_refs excerpts linked to memory_id
    """

    def __init__(
        self,
        data_dir: str | Path,
        embedding_provider: "EmbeddingProvider",
        similarity_threshold: float = DEFAULT_SIMILARITY_THRESHOLD,
    ) -> None:
        self.data_dir = Path(data_dir)
        self.embedding_provider = embedding_provider
        self.similarity_threshold = similarity_threshold
        self._available = False
        self._memories_col: Any = None
        self._evidence_col: Any = None

        self._init_chromadb()

    def _init_chromadb(self) -> None:
        """Initialize ChromaDB client and collections."""
        try:
            import chromadb

            chroma_dir = self.data_dir / "chroma"
            chroma_dir.mkdir(parents=True, exist_ok=True)

            settings = chromadb.Settings(
                anonymized_telemetry=False,
                allow_reset=True,
            )
            self._client = chromadb.PersistentClient(
                path=str(chroma_dir), settings=settings,
            )
            self._memories_col = self._client.get_or_create_collection(
                name="memories",
                metadata={"hnsw:space": "cosine"},
            )
            self._evidence_col = self._client.get_or_create_collection(
                name="evidence",
                metadata={"hnsw:space": "cosine"},
            )
            self._available = True
            logger.info("VectorStore initialized at %s", chroma_dir)
        except ImportError:
            logger.warning("chromadb not installed — vector search disabled")
        except Exception as e:
            logger.warning("VectorStore init failed: %s — vector search disabled", e)

    @property
    def available(self) -> bool:
        """Whether vector search is operational."""
        return self._available

    def close(self) -> None:
        """V1.13: 释放 ChromaDB 资源，关闭 SQLite 连接。

        必须在 VectorStore 不再使用时调用，否则 Windows 上文件锁会阻止删除。
        """
        if not self._available:
            return
        try:
            # 删除 collection 引用释放底层 SQLite 连接
            if self._memories_col is not None:
                del self._memories_col
            if self._evidence_col is not None:
                del self._evidence_col
            # 尝试停止 ChromaDB 系统线程
            if hasattr(self._client, "_system"):
                self._client._system.stop()
            del self._client
            self._available = False
        except Exception:
            pass
        # 强制 GC 释放可能残留的文件句柄
        import gc
        gc.collect()

    def index_item(self, item: "MemoryItem") -> None:
        """Index or update a single MemoryItem in both collections."""
        if not self._available:
            return

        memory_text = f"{item.current_value} {item.rationale}".strip()
        if not memory_text:
            return

        try:
            embedding = self.embedding_provider.embed_single(memory_text)
            self._memories_col.upsert(
                ids=[item.memory_id],
                embeddings=[embedding],
                metadatas=[{
                    "project_id": item.project_id,
                    "state_type": item.state_type,
                    "key": item.key,
                    "owner": item.owner or "",
                    "status": item.status,
                }],
                documents=[memory_text],
            )

            for i, ref in enumerate(item.source_refs):
                if not ref.excerpt.strip():
                    continue
                ref_embedding = self.embedding_provider.embed_single(ref.excerpt)
                ref_id = f"{item.memory_id}__ref_{i}"
                self._evidence_col.upsert(
                    ids=[ref_id],
                    embeddings=[ref_embedding],
                    metadatas=[{
                        "memory_id": item.memory_id,
                        "project_id": item.project_id,
                        "message_id": ref.message_id,
                        "sender_name": ref.sender_name,
                    }],
                    documents=[ref.excerpt],
                )
        except Exception as e:
            logger.warning("Failed to index item %s: %s", item.memory_id, e)

    def index_items(self, items: list["MemoryItem"]) -> int:
        """Batch index multiple MemoryItems. Returns count of successfully indexed."""
        if not self._available:
            return 0
        count = 0
        for item in items:
            self.index_item(item)
            count += 1
        return count

    def remove_item(self, memory_id: str) -> None:
        """Remove a memory item and its evidence from the vector store."""
        if not self._available:
            return
        try:
            self._memories_col.delete(ids=[memory_id])
            existing = self._evidence_col.get(
                where={"memory_id": memory_id},
            )
            if existing and existing["ids"]:
                self._evidence_col.delete(ids=existing["ids"])
        except Exception as e:
            logger.warning("Failed to remove item %s: %s", memory_id, e)

    def search(
        self,
        query: str,
        project_id: str | None = None,
        top_k: int = 10,
        state_type: str | None = None,
        owner: str | None = None,
    ) -> list[tuple[str, float]]:
        """Semantic search over memory items.

        V1.13 OPT-2: 支持 state_type/owner 结构化过滤。

        Returns:
            List of (memory_id, similarity_score) tuples, descending by score.
        """
        if not self._available or not query.strip():
            return []

        try:
            query_embedding = self.embedding_provider.embed_single(query)

            where_filter = self._build_filter(project_id, state_type, owner)

            results = self._memories_col.query(
                query_embeddings=[query_embedding],
                n_results=top_k,
                where=where_filter,
            )

            if not results or not results["ids"] or not results["ids"][0]:
                return []

            scored = []
            ids = results["ids"][0]
            distances = results["distances"][0] if results.get("distances") else []

            for i, memory_id in enumerate(ids):
                similarity = 1.0 - distances[i] if i < len(distances) else 0.0
                if similarity >= self.similarity_threshold:
                    scored.append((memory_id, similarity))

            return scored
        except Exception as e:
            logger.warning("Vector search failed: %s", e)
            return []

    def search_evidence(
        self,
        query: str,
        project_id: str | None = None,
        top_k: int = 20,
        state_type: str | None = None,
        owner: str | None = None,
    ) -> list[tuple[str, float, str]]:
        """Semantic search over evidence excerpts.

        V1.13 OPT-2: 支持 state_type/owner 结构化过滤。

        Returns:
            List of (memory_id, similarity_score, excerpt) tuples.
        """
        if not self._available or not query.strip():
            return []

        try:
            query_embedding = self.embedding_provider.embed_single(query)

            where_filter = self._build_filter(project_id, state_type, owner)

            results = self._evidence_col.query(
                query_embeddings=[query_embedding],
                n_results=top_k,
                where=where_filter,
            )

            if not results or not results["ids"] or not results["ids"][0]:
                return []

            scored = []
            ids = results["ids"][0]
            distances = results["distances"][0] if results.get("distances") else []
            metadatas = results["metadatas"][0] if results.get("metadatas") else []
            documents = results["documents"][0] if results.get("documents") else []

            for i, _ref_id in enumerate(ids):
                similarity = 1.0 - distances[i] if i < len(distances) else 0.0
                if similarity >= self.similarity_threshold:
                    memory_id = metadatas[i].get("memory_id", "") if i < len(metadatas) else ""
                    excerpt = documents[i] if i < len(documents) else ""
                    scored.append((memory_id, similarity, excerpt))

            return scored
        except Exception as e:
            logger.warning("Vector evidence search failed: %s", e)
            return []

    def rebuild_index(self, items: list["MemoryItem"]) -> int:
        """Clear and rebuild the entire index from a list of items."""
        if not self._available:
            return 0

        try:
            existing_mem = self._memories_col.get()
            if existing_mem and existing_mem["ids"]:
                self._memories_col.delete(ids=existing_mem["ids"])

            existing_ev = self._evidence_col.get()
            if existing_ev and existing_ev["ids"]:
                self._evidence_col.delete(ids=existing_ev["ids"])
        except Exception as e:
            logger.warning("Failed to clear index for rebuild: %s", e)
            return 0

        return self.index_items(items)

    @staticmethod
    def _build_filter(
        project_id: str | None = None,
        state_type: str | None = None,
        owner: str | None = None,
    ) -> dict | None:
        """V1.13 OPT-2: 构建 ChromaDB where 过滤条件。

        ChromaDB 多条件需用 $and 包裹。
        """
        conditions = []
        if project_id:
            conditions.append({"project_id": project_id})
        if state_type:
            conditions.append({"state_type": state_type})
        if owner:
            conditions.append({"owner": owner})
        if not conditions:
            return None
        if len(conditions) == 1:
            return conditions[0]
        return {"$and": conditions}

    def stats(self) -> dict[str, Any]:
        """Return current index statistics."""
        if not self._available:
            return {"memories": 0, "evidence": 0, "available": False}
        try:
            return {
                "memories": self._memories_col.count(),
                "evidence": self._evidence_col.count(),
                "available": True,
            }
        except Exception:
            return {"memories": 0, "evidence": 0, "available": False}
