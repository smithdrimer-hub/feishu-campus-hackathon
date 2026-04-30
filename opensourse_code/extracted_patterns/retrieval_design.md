# Retrieval Design — 可借鉴模式

来源：基于 mem0, graphiti, agent-memory-server, cognee 的检索系统分析。

---

## 1. 检索接口抽象

**推荐设计**：

```python
from typing import Protocol, Literal

class SearchRequest(BaseModel):
    """检索请求。"""
    
    # 查询
    query: str | None = None           # 文本查询（语义/关键词搜索用）
    memory_id: str | None = None       # 精确 ID 查询
    
    # 搜索模式
    search_mode: Literal["semantic", "keyword", "hybrid"] = "hybrid"
    hybrid_alpha: float = 0.7          # 混合搜索权重（0=纯关键词，1=纯语义）
    
    # 作用域过滤
    scope: ScopeFilter | None = None
    
    # 内容过滤
    state_types: list[str] | None = None
    statuses: list[str] | None = None
    topics: list[str] | None = None
    entities: list[str] | None = None
    
    # 时间过滤
    created_after: str | None = None
    created_before: str | None = None
    event_date_range: tuple[str, str] | None = None
    
    # 分页
    limit: int = 10
    offset: int = 0
    
    # 高级选项
    threshold: float | None = None     # 分数阈值
    rerank: bool = True                # 是否启用 rerank
    recency_boost: bool = True         # 是否启用时间衰减


class ScoredMemory(BaseModel):
    """带分数的检索结果。"""
    
    memory: MemoryItem
    score: float                       # 归一化分数 0-1
    score_type: Literal["semantic", "keyword", "hybrid"]
    rank: int                          # 最终排名
    explanation: dict | None = None    # 可选的分数解释


class MemorySearcher(Protocol):
    """Memory retrieval abstraction."""
    
    def search(self, request: SearchRequest) -> list[ScoredMemory]:
        """Search memories with filters and scoring."""
        pass
    
    def get(self, memory_id: str) -> MemoryItem | None:
        """Get single memory by ID."""
        pass
    
    def list(self, filters: dict, limit: int, offset: int) -> list[MemoryItem]:
        """List memories with filters."""
        pass
```

**借鉴来源**：
- `mem0/memory/main.py` — `search()` 方法 + 高级过滤
- `agent-memory-server/models.py` — `SearchRequest` 完整字段设计
- `graphiti_core/search/search.py` — 四通道搜索架构

---

## 2. 混合搜索设计（推荐 agent-memory-server）

**三种搜索模式**：

```python
class SearchModeEnum(str, Enum):
    SEMANTIC = "semantic"    # 纯向量语义搜索
    KEYWORD = "keyword"      # 纯 BM25 关键词搜索
    HYBRID = "hybrid"        # 语义 + 关键词融合
```

**混合搜索算法**（RRF - Reciprocal Rank Fusion）：

```python
def hybrid_search(
    semantic_results: list[ScoredMemory],
    keyword_results: list[ScoredMemory],
    alpha: float = 0.7,  # 语义权重
) -> list[ScoredMemory]:
    """
    混合搜索：语义 + 关键词分数融合。
    
    使用 RRF (Reciprocal Rank Fusion)：
    score = alpha * (1 / (rank_semantic + k)) + (1-alpha) * (1 / (rank_keyword + k))
    
    k 是平滑常数，通常取 60。
    """
    k = 60
    
    # 构建排名映射
    semantic_ranks = {m.memory_id: i + 1 for i, m in enumerate(semantic_results)}
    keyword_ranks = {m.memory_id: i + 1 for i, m in enumerate(keyword_results)}
    
    # 融合分数
    fused_scores = {}
    all_ids = set(semantic_ranks.keys()) | set(keyword_ranks.keys())
    
    for mid in all_ids:
        sem_rank = semantic_ranks.get(mid, len(semantic_results) + 1)
        kw_rank = keyword_ranks.get(mid, len(keyword_results) + 1)
        
        sem_score = alpha * (1.0 / (sem_rank + k))
        kw_score = (1 - alpha) * (1.0 / (kw_rank + k))
        
        fused_scores[mid] = sem_score + kw_score
    
    # 按融合分数排序
    sorted_ids = sorted(fused_scores.keys(), key=lambda x: fused_scores[x], reverse=True)
    
    # 构建结果
    results = []
    for rank, mid in enumerate(sorted_ids):
        # 从原始结果中获取 memory
        memory = next(
            (m.memory for m in semantic_results if m.memory_id == mid) or
            (m.memory for m in keyword_results if m.memory_id == mid),
            None
        )
        if memory:
            results.append(ScoredMemory(
                memory=memory,
                score=fused_scores[mid],
                score_type="hybrid",
                rank=rank + 1,
            ))
    
    return results
```

**借鉴来源**：
- `agent-memory-server/long_term_memory.py` — 混合搜索 + RRF 实现
- `mem0/memory/main.py` — 三路融合（semantic + BM25 + entity boost）

---

## 3. 过滤器设计（推荐 agent-memory-server）

**过滤器类型层次**：

```python
from typing import Protocol, Literal

class BaseFilter(Protocol):
    """Base filter protocol."""
    
    def to_query(self) -> dict:
        """Convert filter to backend query format."""
        pass


class SessionId(BaseModel):
    eq: str | None = None
    in_list: list[str] | None = None
    
    def to_query(self) -> dict:
        if self.eq:
            return {"session_id": self.eq}
        if self.in_list:
            return {"session_id": {"$in": self.in_list}}
        return {}


class Namespace(BaseModel):
    eq: str | None = None
    prefix: str | None = None  # 前缀匹配
    
    def to_query(self) -> dict:
        if self.eq:
            return {"namespace": self.eq}
        if self.prefix:
            return {"namespace": {"$regex": f"^{self.prefix}"}}
        return {}


class UserId(BaseModel):
    eq: str | None = None
    in_list: list[str] | None = None
    
    def to_query(self) -> dict:
        if self.eq:
            return {"user_id": self.eq}
        if self.in_list:
            return {"user_id": {"$in": self.in_list}}
        return {}


class Topics(BaseModel):
    contains_all: list[str] | None = None  # AND
    contains_any: list[str] | None = None  # OR
    
    def to_query(self) -> dict:
        if self.contains_all:
            return {"topics": {"$all": self.contains_all}}
        if self.contains_any:
            return {"topics": {"$in": self.contains_any}}
        return {}


class Entities(BaseModel):
    contains_all: list[str] | None = None
    contains_any: list[str] | None = None
    
    def to_query(self) -> dict:
        if self.contains_all:
            return {"entities": {"$all": self.contains_all}}
        if self.contains_any:
            return {"entities": {"$in": self.contains_any}}
        return {}


class CreatedAt(BaseModel):
    gt: str | None = None   # ISO8601
    gte: str | None = None
    lt: str | None = None
    lte: str | None = None
    
    def to_query(self) -> dict:
        filters = {}
        if self.gt:
            filters["created_at"] = {"$gt": self.gt}
        if self.gte:
            filters["created_at"] = {"$gte": self.gte}
        if self.lt:
            filters["created_at"] = filters.get("created_at", {}) | {"$lt": self.lt}
        if self.lte:
            filters["created_at"] = filters.get("created_at", {}) | {"$lte": self.lte}
        return filters


class MemoryType(BaseModel):
    eq: str | None = None
    in_list: list[str] | None = None
    
    def to_query(self) -> dict:
        if self.eq:
            return {"memory_type": self.eq}
        if self.in_list:
            return {"memory_type": {"$in": self.in_list}}
        return {}


class EventDate(BaseModel):
    """Episodic memories 的事件日期过滤。"""
    
    gt: str | None = None
    gte: str | None = None
    lt: str | None = None
    lte: str | None = None
    range: tuple[str, str] | None = None  # (start, end)
    
    def to_query(self) -> dict:
        filters = {}
        if self.range:
            filters["event_date"] = {
                "$gte": self.range[0],
                "$lte": self.range[1]
            }
        if self.gt:
            filters["event_date"] = filters.get("event_date", {}) | {"$gt": self.gt}
        if self.gte:
            filters["event_date"] = filters.get("event_date", {}) | {"$gte": self.gte}
        if self.lt:
            filters["event_date"] = filters.get("event_date", {}) | {"$lt": self.lt}
        if self.lte:
            filters["event_date"] = filters.get("event_date", {}) | {"$lte": self.lte}
        return filters
```

**组合过滤**：

```python
class CompositeFilter:
    """组合多个过滤器。"""
    
    def __init__(
        self,
        AND: list[BaseFilter] | None = None,
        OR: list[BaseFilter] | None = None,
        NOT: BaseFilter | None = None,
    ):
        self.AND = AND or []
        self.OR = OR or []
        self.NOT = NOT
    
    def to_query(self) -> dict:
        query = {}
        
        if self.AND:
            query["$and"] = [f.to_query() for f in self.AND]
        
        if self.OR:
            query["$or"] = [f.to_query() for f in self.OR]
        
        if self.NOT:
            query["$not"] = self.NOT.to_query()
        
        return query
```

**借鉴来源**：
- `agent-memory-server/models.py` — `SearchRequest` 的过滤器字段
- `agent-memory-server/filters.py` — `SessionId`, `Namespace`, `Topics`, `Entities`, `CreatedAt` 等
- `mem0/memory/main.py` — 高级过滤：`eq`, `ne`, `gt`, `gte`, `lt`, `lte`, `in`, `nin`, `contains`, `AND`, `OR`, `NOT`

---

## 4. Recency Re-ranking（推荐 agent-memory-server）

**时间衰减重排序**：

```python
def recency_rerank(
    results: list[ScoredMemory],
    semantic_weight: float = 0.5,
    recency_weight: float = 0.3,
    freshness_weight: float = 0.1,
    novelty_weight: float = 0.1,
    half_life_last_access_days: float = 7.0,
    half_life_created_days: float = 30.0,
) -> list[ScoredMemory]:
    """
    时间感知重排序。
    
    分数 = semantic_weight * semantic_score
         + recency_weight * recency_score
         + freshness_weight * freshness_score
         + novelty_weight * novelty_score
    """
    now = datetime.now()
    
    for result in results:
        memory = result.memory
        
        # 1. Last accessed decay (最近访问越近，分数越高)
        last_access = parse_iso8601(memory.last_accessed)
        days_since_access = (now - last_access).days
        access_half_life = half_life_last_access_days
        access_score = 0.5 ** (days_since_access / access_half_life)
        
        # 2. Created decay (创建越近，分数越高)
        created = parse_iso8601(memory.created_at)
        days_since_created = (now - created).days
        created_half_life = half_life_created_days
        created_score = 0.5 ** (days_since_created / created_half_life)
        
        # 3. Freshness (更新时间与创建时间的差距，差距小说明内容稳定)
        updated = parse_iso8601(memory.updated_at)
        age = (updated - created).days
        freshness_score = 1.0 / (1.0 + age / 30)  # 30 天为尺度
        
        # 4. Novelty (版本越低越新颖)
        novelty_score = 1.0 / memory.version
        
        # 融合分数
        recency_score = (
            semantic_weight * result.score +
            recency_weight * access_score +
            freshness_weight * freshness_score +
            novelty_weight * novelty_score
        )
        
        result.score = recency_score
    
    # 重新排序
    results.sort(key=lambda x: x.score, reverse=True)
    
    # 更新排名
    for i, result in enumerate(results):
        result.rank = i + 1
    
    return results
```

**借鉴来源**：
- `agent-memory-server/models.py` — `SearchRequest` 的 recency_boost 参数
- `agent-memory-server/utils/recency.py` — recency rerank 实现

---

## 5. 多 Scope 检索（推荐 cognee）

**RecallScope 系统**（`cognee/modules/retrieval/`）：

```python
class RecallScope(str, Enum):
    GRAPH = "graph"           # 永久知识图谱
    SESSION = "session"       # 会话缓存
    TRACE = "trace"           # 操作轨迹
    GRAPH_CONTEXT = "graph_context"  # 图谱上下文
    ALL = "all"               # 全部来源


def recall(
    query: str,
    scope: RecallScope = RecallScope.ALL,
    filters: dict | None = None,
    top_k: int = 10,
) -> list[ScoredMemory]:
    """
    多源检索，自动路由。
    """
    results = []
    
    if scope == RecallScope.SESSION or scope == RecallScope.ALL:
        session_results = search_session_cache(query, filters, top_k)
        results.extend(session_results)
    
    if scope == RecallScope.GRAPH or scope == RecallScope.GRAPH_CONTEXT or scope == RecallScope.ALL:
        graph_results = search_knowledge_graph(query, filters, top_k)
        results.extend(graph_results)
    
    if scope == RecallScope.TRACE or scope == RecallScope.ALL:
        trace_results = search_trace_logs(query, filters, top_k)
        results.extend(trace_results)
    
    # 融合 + 去重
    return deduplicate_and_rerank(results)
```

**借鉴来源**：
- `cognee/api/v1/recall/recall.py` — `recall()` 多源路由
- `cognee/modules/retrieval/` — 15+ 种 SearchType

---

## 6. 四通道并行搜索（推荐 graphiti）

**架构**：

```
Query
  │
  ├────────────────────────────────────┐
  │                                    │
  ▼                                    ▼
Edge Search                      Node Search
(BM25 + cosine + BFS)          (BM25 + cosine + BFS)
  │                                    │
  ├────────────────────────────────────┤
  │                                    │
  ▼                                    ▼
Episode Search                   Community Search
(BM25 only)                    (BM25 + cosine)
  │                                    │
  └────────────────┬───────────────────┘
                   │
                   ▼
              RRF Fusion + Rerank
                   │
                   ▼
              SearchResults
```

**实现**：

```python
async def search(
    query: str,
    config: SearchConfig,
) -> SearchResults:
    """四通道并行搜索。"""
    
    # 并行执行四个搜索
    edge_task = search_edges(query, config.edge)
    node_task = search_nodes(query, config.node)
    episode_task = search_episodes(query, config.episode)
    community_task = search_communities(query, config.community)
    
    edge_results, node_results, episode_results, community_results = await asyncio.gather(
        edge_task, node_task, episode_task, community_task
    )
    
    # RRF 融合
    reranked_edges = apply_reranker(edge_results, config.edge.reranker)
    reranked_nodes = apply_reranker(node_results, config.node.reranker)
    reranked_episodes = apply_reranker(episode_results, config.episode.reranker)
    reranked_communities = apply_reranker(community_results, config.community.reranker)
    
    return SearchResults(
        edges=reranked_edges,
        nodes=reranked_nodes,
        episodes=reranked_episodes,
        communities=reranked_communities,
    )
```

**借鉴来源**：
- `graphiti_core/search/search.py` — 四通道并行搜索
- `graphiti_core/search/search_config.py` — `SearchConfig`, `SearchResults`

---

## 7. 推荐飞书 Memory Engine 的检索设计

```python
class FeishuMemorySearcher:
    """飞书 Memory Engine 检索器。"""
    
    def __init__(self, store: MemoryStore):
        self.store = store
    
    def search(
        self,
        query: str | None = None,
        filters: dict | None = None,
        top_k: int = 10,
        search_mode: Literal["semantic", "keyword", "hybrid"] = "hybrid",
        recency_boost: bool = True,
    ) -> list[ScoredMemory]:
        """搜索记忆。"""
        
        # 1. 解析过滤器
        scope_filter = self._parse_scope_filter(filters)
        content_filter = self._parse_content_filter(filters)
        time_filter = self._parse_time_filter(filters)
        
        # 2. 执行搜索
        if search_mode == "semantic":
            results = self._semantic_search(query, scope_filter, top_k)
        elif search_mode == "keyword":
            results = self._keyword_search(query, scope_filter, top_k)
        else:  # hybrid
            semantic = self._semantic_search(query, scope_filter, top_k * 2)
            keyword = self._keyword_search(query, scope_filter, top_k * 2)
            results = hybrid_search(semantic, keyword, alpha=0.7)
        
        # 3. 应用过滤
        results = self._apply_filters(results, content_filter, time_filter)
        
        # 4. Recency re-rank
        if recency_boost:
            results = recency_rerank(results)
        
        # 5. 截断到 top_k
        return results[:top_k]
    
    def _parse_scope_filter(self, filters: dict) -> ScopeFilter:
        """解析作用域过滤器。"""
        return ScopeFilter(
            project_id=filters.get("project_id"),
            scope_type=filters.get("scope_type"),
            user_id=filters.get("user_id"),
        )
    
    def _parse_content_filter(self, filters: dict) -> CompositeFilter:
        """解析内容过滤器。"""
        return CompositeFilter(
            AND=[
                Topics(contains_any=filters.get("topics")),
                Entities(contains_any=filters.get("entities")),
                MemoryType(in_list=filters.get("state_types")),
            ]
        )
    
    def _parse_time_filter(self, filters: dict) -> CompositeFilter:
        """解析时间过滤器。"""
        return CompositeFilter(
            AND=[
                CreatedAt(
                    gte=filters.get("created_after"),
                    lte=filters.get("created_before"),
                ),
                EventDate(range=filters.get("event_date_range")),
            ]
        )
```

---

## 8. 总结：关键借鉴点

| 项目 | 检索设计亮点 | 飞书可借鉴 |
|------|-------------|-----------|
| **mem0** | 高级过滤（AND/OR/NOT + 比较运算符），三路融合 | 过滤器设计，混合搜索 |
| **graphiti** | 四通道并行搜索，RRF + cross_encoder rerank | 可插拔 reranker 架构 |
| **agent-memory-server** | 混合搜索 RRF 实现，recency re-ranking，完整过滤器类型 | 混合搜索，时间衰减，过滤器 |
| **cognee** | RecallScope 多源路由，15+ SearchType | 多作用域检索 |
