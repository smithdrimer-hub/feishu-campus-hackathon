"""MCP server exposing OpenClaw Memory Engine as agent-callable tools.

Usage:
  python scripts/mcp_server.py --data-dir data/demo --project-id aurora-sprint

Protocol: MCP (JSON-RPC over stdio)
Tools exposed:
  - get_project_state: Full structured project state (decisions, tasks, risks, etc.)
  - search_memories: Semantic search across memory items
"""

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from memory.store import MemoryStore
from memory.engine import MemoryEngine
from memory.extractor import RuleBasedExtractor


def _init_engine(data_dir: str) -> MemoryEngine:
    store = MemoryStore(Path(data_dir))
    return MemoryEngine(store, RuleBasedExtractor())


def _handle_get_project_state(engine: MemoryEngine, project_id: str) -> dict:
    from memory.project_state import build_agent_context_pack
    items = engine.store.list_items(project_id)
    return build_agent_context_pack(project_id, items)


def _handle_search_memories(engine: MemoryEngine, query: str, project_id: str) -> dict:
    vs = getattr(engine, "vector_store", None)
    if vs is None or not getattr(vs, "available", False):
        # Fallback to keyword search
        results = engine.store.search_keywords(query, project_id=project_id, top_k=10)
        return {
            "query": query, "method": "keyword",
            "results": [
                {"memory_id": r[0].memory_id, "state_type": r[0].state_type,
                 "value": r[0].current_value[:200], "score": r[1]}
                for r in results
            ],
        }
    results = vs.search(query, project_id=project_id, top_k=10)
    items = engine.store.list_items(project_id)
    item_map = {i.memory_id: i for i in items}
    return {
        "query": query, "method": "vector",
        "results": [
            {"memory_id": mid, "state_type": item_map[mid].state_type,
             "value": item_map[mid].current_value[:200], "score": round(score, 3)}
            for mid, score in results if mid in item_map
        ],
    }


def main():
    import argparse
    parser = argparse.ArgumentParser(description="OpenClaw MCP Server")
    parser.add_argument("--data-dir", default="data/demo")
    parser.add_argument("--project-id", default="aurora-sprint")
    args = parser.parse_args()

    engine = _init_engine(args.data_dir)

    # Read JSON-RPC requests from stdin, write responses to stdout
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            request = json.loads(line)
        except json.JSONDecodeError:
            continue

        req_id = request.get("id")
        method = request.get("method", "")
        params = request.get("params", {}) or {}

        if method == "initialize":
            response = {
                "jsonrpc": "2.0", "id": req_id,
                "result": {
                    "protocolVersion": "2024-11-05",
                    "serverInfo": {"name": "openclaw-memory", "version": "1.21"},
                    "capabilities": {"tools": {}},
                },
            }
        elif method == "tools/list":
            response = {
                "jsonrpc": "2.0", "id": req_id,
                "result": {
                    "tools": [
                        {
                            "name": "get_project_state",
                            "description": "Get full structured project state including decisions, tasks, risks, blockers, and discussion snippets. Returns JSON with all active collaboration memory.",
                            "inputSchema": {
                                "type": "object",
                                "properties": {
                                    "project_id": {"type": "string", "description": "Project identifier (e.g. 'aurora-sprint')"}
                                },
                                "required": ["project_id"],
                            },
                        },
                        {
                            "name": "search_memories",
                            "description": "Semantic search across project memories. Find relevant decisions, blockers, tasks by natural language query.",
                            "inputSchema": {
                                "type": "object",
                                "properties": {
                                    "query": {"type": "string", "description": "Natural language search query"},
                                    "project_id": {"type": "string", "description": "Project identifier"},
                                },
                                "required": ["query", "project_id"],
                            },
                        },
                    ]
                },
            }
        elif method == "tools/call":
            tool_name = params.get("name", "")
            tool_args = params.get("arguments", {}) or {}
            pid = tool_args.get("project_id", args.project_id)

            try:
                if tool_name == "get_project_state":
                    result = _handle_get_project_state(engine, pid)
                elif tool_name == "search_memories":
                    result = _handle_search_memories(engine, tool_args.get("query", ""), pid)
                else:
                    result = {"error": f"Unknown tool: {tool_name}"}
            except Exception as e:
                result = {"error": str(e)}

            response = {
                "jsonrpc": "2.0", "id": req_id,
                "result": {"content": [{"type": "text", "text": json.dumps(result, ensure_ascii=False, default=str)}]},
            }
        else:
            response = {"jsonrpc": "2.0", "id": req_id, "error": {"code": -32601, "message": f"Unknown method: {method}"}}

        sys.stdout.write(json.dumps(response, ensure_ascii=False) + "\n")
        sys.stdout.flush()


if __name__ == "__main__":
    main()
