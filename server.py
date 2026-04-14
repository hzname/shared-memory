"""
Shared Memory MCP Server — Obsidian Vault + Semantic Search

MCP server providing shared long-term memory for multiple AI agents
(Hermes, OpenClaw, QwenCode) backed by an Obsidian-compatible markdown vault.

Storage:  Obsidian vault (markdown + YAML frontmatter)
Search:   SQLite FTS5 (full-text) + sqlite-vec (vector/semantic)
Embeds:   sentence-transformers (all-MiniLM-L6-v2)

Usage:
    python3 server.py                          # stdio (default)
    python3 server.py --transport sse --port 8765  # SSE over HTTP
"""

import argparse
import datetime
import glob
import hashlib
import json
import os
import re
import sqlite3
import sys
import threading
from pathlib import Path
from typing import Any, Optional

import yaml
from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import (
    TextContent,
    Tool,
)

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

VAULT_DIR = os.environ.get("SHARED_MEMORY_VAULT", os.path.expanduser("~/shared-memory/vault"))
DB_PATH = os.environ.get("SHARED_MEMORY_DB", os.path.expanduser("~/shared-memory/db/memory.db"))
EMBED_MODEL = os.environ.get("SHARED_MEMORY_EMBED_MODEL", "all-MiniLM-L6-v2")
EMBED_DIM = 384  # all-MiniLM-L6-v2 output dimension

# ---------------------------------------------------------------------------
# Embedding engine (lazy-loaded)
# ---------------------------------------------------------------------------

_embed_model = None
_embed_lock = threading.Lock()


def get_embed_model():
    global _embed_model
    if _embed_model is None:
        with _embed_lock:
            if _embed_model is None:
                from sentence_transformers import SentenceTransformer
                _embed_model = SentenceTransformer(EMBED_MODEL)
    return _embed_model


def embed_text(text: str) -> list[float]:
    model = get_embed_model()
    return model.encode(text, normalize_embeddings=True).tolist()


# ---------------------------------------------------------------------------
# YAML frontmatter parser
# ---------------------------------------------------------------------------

FM_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)


def parse_frontmatter(content: str) -> tuple[dict, str]:
    """Return (frontmatter_dict, body_text)."""
    m = FM_RE.match(content)
    if m:
        try:
            fm = yaml.safe_load(m.group(1)) or {}
        except yaml.YAMLError:
            fm = {}
        body = content[m.end():]
    else:
        fm = {}
        body = content
    return fm, body.strip()


def build_content(fm: dict, body: str) -> str:
    """Serialize frontmatter + body back to markdown."""
    clean = {k: v for k, v in fm.items() if v is not None}
    fm_str = yaml.dump(clean, allow_unicode=True, default_flow_style=False).strip()
    return f"---\n{fm_str}\n---\n\n{body.strip()}\n"


# ---------------------------------------------------------------------------
# Vault helpers
# ---------------------------------------------------------------------------

VAULT_SUBDIRS = ["knowledge", "decisions", "tasks", "journal", "agents"]


def init_vault(vault_dir: str = VAULT_DIR):
    """Create vault directory structure if missing."""
    for d in VAULT_SUBDIRS:
        os.makedirs(os.path.join(vault_dir, d), exist_ok=True)


def vault_path(note_id: str, category: Optional[str] = None) -> str:
    """Resolve note_id to a vault file path.
    
    note_id can be:
      - 'category/slug'  -> vault/category/slug.md
      - 'slug'           -> search all categories
    """
    if "/" in note_id:
        parts = note_id.split("/", 1)
        cat, slug = parts[0], parts[1]
    elif category:
        cat, slug = category, note_id
    else:
        # search all categories
        for d in VAULT_SUBDIRS:
            candidate = os.path.join(VAULT_DIR, d, f"{note_id}.md")
            if os.path.exists(candidate):
                return candidate
        # default to knowledge
        cat, slug = "knowledge", note_id

    if not slug.endswith(".md"):
        slug += ".md"
    return os.path.join(VAULT_DIR, cat, slug)


def list_all_notes(vault_dir: str = VAULT_DIR) -> list[dict]:
    """List all notes in vault with frontmatter metadata."""
    notes = []
    for root, _dirs, files in os.walk(vault_dir):
        for f in files:
            if not f.endswith(".md"):
                continue
            fpath = os.path.join(root, f)
            rel = os.path.relpath(fpath, vault_dir)
            note_id = rel[:-3]  # strip .md
            try:
                with open(fpath, "r", encoding="utf-8") as fh:
                    content = fh.read()
                fm, body = parse_frontmatter(content)
                notes.append({
                    "id": note_id,
                    "path": fpath,
                    "title": fm.get("title", note_id.split("/")[-1]),
                    "type": fm.get("type", ""),
                    "tags": fm.get("tags", []),
                    "updated_by": fm.get("updated_by", ""),
                    "updated": fm.get("updated", ""),
                    "confidence": fm.get("confidence", ""),
                    "status": fm.get("status", "active"),
                    "summary": body[:200],
                })
            except Exception:
                pass
    return notes


def extract_wikilinks(text: str) -> list[str]:
    """Extract [[Wikilink]] targets from text."""
    return list(set(re.findall(r"\[\[([^\]]+)\]\]", text)))


# ---------------------------------------------------------------------------
# SQLite + FTS5 + sqlite-vec index
# ---------------------------------------------------------------------------

_db_lock = threading.Lock()


def get_db() -> sqlite3.Connection:
    """Get a thread-local DB connection with FTS5 + vec."""
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db():
    """Create tables if they don't exist."""
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = get_db()

    conn.execute("""
        CREATE TABLE IF NOT EXISTS notes (
            note_id TEXT PRIMARY KEY,
            title TEXT,
            category TEXT,
            type TEXT DEFAULT '',
            tags TEXT DEFAULT '[]',
            updated_by TEXT DEFAULT '',
            updated TEXT DEFAULT '',
            created TEXT DEFAULT '',
            confidence TEXT DEFAULT '',
            status TEXT DEFAULT 'active',
            body_hash TEXT DEFAULT '',
            embedding BLOB,
            UNIQUE(note_id)
        )
    """)

    # FTS5 full-text index (standalone content table)
    conn.execute("""
        CREATE VIRTUAL TABLE IF NOT EXISTS notes_fts USING fts5(
            note_id,
            title,
            body,
            tags
        )
    """)

    # sqlite-vec virtual table for vector search
    try:
        conn.enable_load_extension(True)
        import sqlite_vec
        conn.load_extension(sqlite_vec.loadable_path())
        conn.execute(f"""
            CREATE VIRTUAL TABLE IF NOT EXISTS notes_vec USING vec0(
                note_id TEXT PRIMARY KEY,
                embedding float[{EMBED_DIM}]
            )
        """)
    except Exception:
        # sqlite-vec not available — vector search disabled
        pass

    conn.commit()
    conn.close()


def index_note(note_id: str, title: str, body: str, fm: dict):
    """Insert or update a note in the search index."""
    conn = get_db()
    tags_json = json.dumps(fm.get("tags", []), ensure_ascii=False)
    category = note_id.split("/")[0] if "/" in note_id else ""
    body_hash = hashlib.md5(body.encode()).hexdigest()

    # Generate embedding
    embed_text_input = f"{title}\n{body}"[:2000]
    try:
        embedding = embed_text(embed_text_input)
        embed_blob = sqlite3.Binary(
            __import__("struct").pack(f"{len(embedding)}f", *embedding)
        )
    except Exception:
        embed_blob = None

    with _db_lock:
        conn.execute("""
            INSERT INTO notes (note_id, title, category, type, tags, updated_by,
                               updated, created, confidence, status, body_hash, embedding)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(note_id) DO UPDATE SET
                title=excluded.title, category=excluded.category, type=excluded.type,
                tags=excluded.tags, updated_by=excluded.updated_by, updated=excluded.updated,
                created=excluded.created, confidence=excluded.confidence,
                status=excluded.status, body_hash=excluded.body_hash, embedding=excluded.embedding
        """, (note_id, title, category, fm.get("type", ""), tags_json,
              fm.get("updated_by", ""), fm.get("updated", ""), fm.get("created", ""),
              fm.get("confidence", ""), fm.get("status", "active"), body_hash, embed_blob))

        # FTS5 (no UPSERT support — delete+insert)
        conn.execute("DELETE FROM notes_fts WHERE note_id = ?", (note_id,))
        conn.execute("""
            INSERT INTO notes_fts (note_id, title, body, tags)
            VALUES (?, ?, ?, ?)
        """, (note_id, title, body, " ".join(fm.get("tags", []))))

        # sqlite-vec
        if embed_blob:
            try:
                conn.execute("DELETE FROM notes_vec WHERE note_id = ?", (note_id,))
                conn.execute("INSERT INTO notes_vec (note_id, embedding) VALUES (?, ?)",
                             (note_id, embed_blob))
            except Exception:
                pass

        conn.commit()
    conn.close()


def search_fts(query: str, limit: int = 10) -> list[dict]:
    """Full-text search via FTS5."""
    conn = get_db()
    try:
        rows = conn.execute("""
            SELECT n.note_id, n.title, n.type, n.tags, n.updated_by, n.updated,
                   n.confidence, n.status, snippet(notes_fts, 2, '>>', '<<', '...', 40) as snippet
            FROM notes_fts f
            JOIN notes n ON n.note_id = f.note_id
            WHERE notes_fts MATCH ?
            ORDER BY rank
            LIMIT ?
        """, (query, limit)).fetchall()
    except Exception:
        rows = []
    conn.close()

    results = []
    for r in rows:
        results.append({
            "id": r[0], "title": r[1], "type": r[2],
            "tags": json.loads(r[3]) if r[3] else [],
            "updated_by": r[4], "updated": r[5],
            "confidence": r[6], "status": r[7],
            "snippet": r[8],
        })
    return results


def search_vector(query: str, limit: int = 10) -> list[dict]:
    """Semantic vector search via sqlite-vec."""
    conn = get_db()
    try:
        conn.enable_load_extension(True)
        import sqlite_vec
        conn.load_extension(sqlite_vec.loadable_path())
    except Exception:
        conn.close()
        return []

    q_embed = embed_text(query)
    q_blob = sqlite3.Binary(
        __import__("struct").pack(f"{len(q_embed)}f", *q_embed)
    )

    try:
        rows = conn.execute("""
            SELECT v.note_id, n.title, n.type, n.tags, n.updated_by, n.updated,
                   n.confidence, n.status, v.distance
            FROM notes_vec v
            JOIN notes n ON n.note_id = v.note_id
            WHERE v.embedding MATCH ?
            ORDER BY v.distance
            LIMIT ?
        """, (q_blob, limit)).fetchall()
    except Exception:
        rows = []
    conn.close()

    results = []
    for r in rows:
        results.append({
            "id": r[0], "title": r[1], "type": r[2],
            "tags": json.loads(r[3]) if r[3] else [],
            "updated_by": r[4], "updated": r[5],
            "confidence": r[6], "status": r[7],
            "distance": round(r[8], 4),
        })
    return results


def reindex_all():
    """Full reindex: scan vault, rebuild SQLite index."""
    init_db()
    notes = list_all_notes()
    for n in notes:
        note_id = n["id"]
        with open(n["path"], "r", encoding="utf-8") as f:
            content = f.read()
        fm, body = parse_frontmatter(content)
        index_note(note_id, n["title"], body, fm)
    return len(notes)


# ---------------------------------------------------------------------------
# MCP Server
# ---------------------------------------------------------------------------

server = Server("shared-memory")


@server.list_tools()
async def list_tools() -> list[Tool]:
    return [
        Tool(
            name="memory_write",
            description="Create or update a note in the shared memory vault. "
                        "note_id format: 'category/slug' (e.g. 'knowledge/esrm', 'decisions/2026-04-14-cooling'). "
                        "Categories: knowledge, decisions, tasks, journal, agents.",
            inputSchema={
                "type": "object",
                "properties": {
                    "note_id": {"type": "string", "description": "Note ID: category/slug"},
                    "content": {"type": "string", "description": "Markdown body content"},
                    "title": {"type": "string", "description": "Note title (default: slug from note_id)"},
                    "tags": {"type": "array", "items": {"type": "string"}, "description": "Tags"},
                    "type": {"type": "string", "enum": ["fact", "decision", "task", "knowledge", "journal"], "description": "Note type"},
                    "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
                    "agent": {"type": "string", "description": "Agent name writing this note (e.g. Hermes, OpenClaw, QwenCode)"},
                    "status": {"type": "string", "enum": ["active", "archived", "deprecated"], "default": "active"},
                },
                "required": ["note_id", "content"],
            },
        ),
        Tool(
            name="memory_read",
            description="Read a note from the shared memory vault by note_id (e.g. 'knowledge/esrm').",
            inputSchema={
                "type": "object",
                "properties": {
                    "note_id": {"type": "string", "description": "Note ID: category/slug"},
                },
                "required": ["note_id"],
            },
        ),
        Tool(
            name="memory_search",
            description="Search the shared memory vault. Uses hybrid search: "
                        "full-text (FTS5) + semantic vector search when available. "
                        "Returns matching notes with snippets.",
            inputSchema={
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Search query (natural language or keywords)"},
                    "limit": {"type": "integer", "description": "Max results (default 10)", "default": 10},
                    "mode": {"type": "string", "enum": ["hybrid", "fts", "vector"], "default": "hybrid",
                             "description": "Search mode: hybrid (both), fts (full-text only), vector (semantic only)"},
                },
                "required": ["query"],
            },
        ),
        Tool(
            name="memory_list",
            description="List notes in the shared memory vault, optionally filtered by category, type, or tag.",
            inputSchema={
                "type": "object",
                "properties": {
                    "category": {"type": "string", "description": "Filter by category: knowledge, decisions, tasks, journal, agents"},
                    "type": {"type": "string", "description": "Filter by type: fact, decision, task, knowledge, journal"},
                    "tag": {"type": "string", "description": "Filter by tag"},
                    "agent": {"type": "string", "description": "Filter by agent (updated_by)"},
                    "limit": {"type": "integer", "description": "Max results (default 20)", "default": 20},
                },
            },
        ),
        Tool(
            name="memory_delete",
            description="Delete a note from the shared memory vault.",
            inputSchema={
                "type": "object",
                "properties": {
                    "note_id": {"type": "string", "description": "Note ID: category/slug"},
                },
                "required": ["note_id"],
            },
        ),
        Tool(
            name="memory_recent",
            description="Get recently updated notes from the shared memory vault.",
            inputSchema={
                "type": "object",
                "properties": {
                    "limit": {"type": "integer", "description": "Max results (default 10)", "default": 10},
                },
            },
        ),
        Tool(
            name="memory_graph",
            description="Get linked notes (via [[wikilinks]]) for a given note, or get the full knowledge graph.",
            inputSchema={
                "type": "object",
                "properties": {
                    "note_id": {"type": "string", "description": "Note ID to get links for (omit for full graph)"},
                },
            },
        ),
        Tool(
            name="memory_reindex",
            description="Reindex all notes in the vault. Run after manual vault edits or initial setup.",
            inputSchema={
                "type": "object",
                "properties": {},
            },
        ),
    ]


async def _write(note_id: str, content: str, title: Optional[str] = None,
                  tags: Optional[list] = None, note_type: Optional[str] = None,
                  confidence: Optional[str] = None, agent: Optional[str] = None,
                  status: str = "active") -> str:
    """Core write logic."""
    init_vault()
    fpath = vault_path(note_id)
    now = datetime.datetime.now().isoformat(timespec="seconds")

    # Read existing frontmatter if file exists
    existing_fm = {}
    if os.path.exists(fpath):
        with open(fpath, "r", encoding="utf-8") as f:
            existing_content = f.read()
        existing_fm, _ = parse_frontmatter(existing_content)

    slug = note_id.split("/")[-1] if "/" in note_id else note_id
    fm = {
        "title": title or slug.replace("-", " ").replace("_", " ").title(),
        "type": note_type or existing_fm.get("type", "fact"),
        "tags": tags or existing_fm.get("tags", []),
        "created": existing_fm.get("created", now),
        "updated": now,
        "updated_by": agent or existing_fm.get("updated_by", "unknown"),
        "confidence": confidence or existing_fm.get("confidence", ""),
        "status": status,
    }

    md = build_content(fm, content)
    os.makedirs(os.path.dirname(fpath), exist_ok=True)
    with open(fpath, "w", encoding="utf-8") as f:
        f.write(md)

    # Update search index
    init_db()
    index_note(note_id, fm["title"], content, fm)

    return fpath


async def _read(note_id: str) -> Optional[str]:
    """Core read logic."""
    fpath = vault_path(note_id)
    if not os.path.exists(fpath):
        return None
    with open(fpath, "r", encoding="utf-8") as f:
        return f.read()


@server.call_tool()
async def call_tool(name: str, arguments: dict[str, Any]) -> list[TextContent]:
    if name == "memory_write":
        fpath = await _write(
            note_id=arguments["note_id"],
            content=arguments["content"],
            title=arguments.get("title"),
            tags=arguments.get("tags"),
            note_type=arguments.get("type"),
            confidence=arguments.get("confidence"),
            agent=arguments.get("agent"),
            status=arguments.get("status", "active"),
        )
        return [TextContent(type="text", text=f"Written: {fpath}")]

    elif name == "memory_read":
        content = await _read(arguments["note_id"])
        if content is None:
            return [TextContent(type="text", text=f"Note not found: {arguments['note_id']}")]
        return [TextContent(type="text", text=content)]

    elif name == "memory_search":
        query = arguments["query"]
        limit = arguments.get("limit", 10)
        mode = arguments.get("mode", "hybrid")

        results = []
        if mode in ("hybrid", "fts"):
            results.extend(search_fts(query, limit))
        if mode in ("hybrid", "vector"):
            vec_results = search_vector(query, limit)
            # Deduplicate with FTS results
            seen_ids = {r["id"] for r in results}
            for vr in vec_results:
                if vr["id"] not in seen_ids:
                    results.append(vr)
                    seen_ids.add(vr["id"])

        if not results:
            return [TextContent(type="text", text="No results found.")]

        # Format output
        lines = [f"Found {len(results)} note(s):\n"]
        for r in results[:limit]:
            lines.append(f"## {r.get('title', r['id'])} [{r.get('id', '')}]")
            if r.get("tags"):
                lines.append(f"  Tags: {', '.join(r['tags'])}")
            if r.get("updated_by"):
                lines.append(f"  By: {r['updated_by']} | Updated: {r.get('updated', '')}")
            if r.get("snippet"):
                lines.append(f"  >> {r['snippet']}")
            if r.get("distance") is not None:
                lines.append(f"  Distance: {r['distance']}")
            lines.append("")

        return [TextContent(type="text", text="\n".join(lines))]

    elif name == "memory_list":
        category = arguments.get("category")
        note_type = arguments.get("type")
        tag = arguments.get("tag")
        agent = arguments.get("agent")
        limit = arguments.get("limit", 20)

        notes = list_all_notes()

        if category:
            notes = [n for n in notes if n["id"].startswith(category + "/")]
        if note_type:
            notes = [n for n in notes if n.get("type") == note_type]
        if tag:
            notes = [n for n in notes if tag in n.get("tags", [])]
        if agent:
            notes = [n for n in notes if n.get("updated_by") == agent]

        # Sort by updated desc
        notes.sort(key=lambda n: n.get("updated", ""), reverse=True)
        notes = notes[:limit]

        if not notes:
            return [TextContent(type="text", text="No notes found.")]

        lines = [f"{len(notes)} note(s):\n"]
        for n in notes:
            lines.append(f"  [{n['id']}] {n.get('title', n['id'])} "
                         f"({n.get('type', '')}) by {n.get('updated_by', '?')} "
                         f"tags={n.get('tags', [])}")

        return [TextContent(type="text", text="\n".join(lines))]

    elif name == "memory_delete":
        note_id = arguments["note_id"]
        fpath = vault_path(note_id)
        if not os.path.exists(fpath):
            return [TextContent(type="text", text=f"Note not found: {note_id}")]
        os.remove(fpath)

        # Remove from index
        conn = get_db()
        with _db_lock:
            conn.execute("DELETE FROM notes WHERE note_id = ?", (note_id,))
            conn.execute("DELETE FROM notes_fts WHERE note_id = ?", (note_id,))
            try:
                conn.execute("DELETE FROM notes_vec WHERE note_id = ?", (note_id,))
            except Exception:
                pass
            conn.commit()
        conn.close()

        return [TextContent(type="text", text=f"Deleted: {note_id}")]

    elif name == "memory_recent":
        limit = arguments.get("limit", 10)
        notes = list_all_notes()
        notes.sort(key=lambda n: n.get("updated", ""), reverse=True)
        notes = notes[:limit]

        lines = [f"Recent {len(notes)} note(s):\n"]
        for n in notes:
            lines.append(f"  [{n['id']}] {n.get('title', n['id'])} "
                         f"by {n.get('updated_by', '?')} at {n.get('updated', '')}")

        return [TextContent(type="text", text="\n".join(lines))]

    elif name == "memory_graph":
        note_id = arguments.get("note_id")

        if note_id:
            # Get links for a specific note
            fpath = vault_path(note_id)
            if not os.path.exists(fpath):
                return [TextContent(type="text", text=f"Note not found: {note_id}")]
            with open(fpath, "r", encoding="utf-8") as f:
                content = f.read()
            _, body = parse_frontmatter(content)
            links = extract_wikilinks(body)

            # Also find notes that link TO this note
            backlinks = []
            all_notes = list_all_notes()
            for n in all_notes:
                if n["id"] == note_id:
                    continue
                with open(n["path"], "r", encoding="utf-8") as f:
                    c = f.read()
                _, b = parse_frontmatter(c)
                if note_id in extract_wikilinks(b) or note_id.split("/")[-1] in extract_wikilinks(b):
                    backlinks.append(n["id"])

            lines = [f"## Links from [{note_id}]"]
            for l in links:
                lines.append(f"  -> {l}")
            lines.append(f"\n## Backlinks to [{note_id}]")
            for bl in backlinks:
                lines.append(f"  <- {bl}")

            return [TextContent(type="text", text="\n".join(lines))]
        else:
            # Full graph
            all_notes = list_all_notes()
            edges = []
            for n in all_notes:
                with open(n["path"], "r", encoding="utf-8") as f:
                    content = f.read()
                _, body = parse_frontmatter(content)
                for link in extract_wikilinks(body):
                    edges.append((n["id"], link))

            lines = [f"Knowledge graph: {len(all_notes)} nodes, {len(edges)} edges\n"]
            for src, dst in edges:
                lines.append(f"  {src} -> {dst}")

            return [TextContent(type="text", text="\n".join(lines))]

    elif name == "memory_reindex":
        count = reindex_all()
        return [TextContent(type="text", text=f"Reindexed {count} notes.")]

    else:
        return [TextContent(type="text", text=f"Unknown tool: {name}")]


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

async def main():
    init_vault()
    init_db()

    parser = argparse.ArgumentParser(description="Shared Memory MCP Server")
    parser.add_argument("--transport", choices=["stdio", "sse"], default="stdio")
    parser.add_argument("--port", type=int, default=8765, help="Port for SSE transport")
    args = parser.parse_args()

    if args.transport == "stdio":
        async with stdio_server() as (read_stream, write_stream):
            await server.run(read_stream, write_stream, server.create_initialization_options())
    else:
        from mcp.server.sse import SseServerTransport
        from starlette.applications import Starlette
        import uvicorn

        sse = SseServerTransport("/messages")
        app = Starlette(
            routes=[
                sse.get_sse_endpoint("/sse"),
                sse.get_post_endpoint("/messages"),
            ],
        )
        uvicorn.run(app, host="0.0.0.0", port=args.port)


if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
