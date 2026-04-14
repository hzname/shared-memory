# Shared Memory MCP Server

Shared long-term memory for AI agents — Hermes, OpenClaw, QwenCode.

Backed by an **Obsidian-compatible markdown vault** with **semantic search** (FTS5 + vector embeddings via sqlite-vec).

## Architecture

```
┌─────────┐    ┌──────────┐    ┌───────────┐
│ Hermes  │    │ OpenClaw │    │ QwenCode  │
│ MCP     │    │ Gateway  │    │ MCP       │
│ client  │    │ + Memory │    │ client    │
└────┬─────┘    └────┬─────┘    └─────┬─────┘
     │               │               │
     └───────────────┼───────────────┘
                     │ MCP Protocol (stdio / SSE)
              ┌──────▼──────┐
              │ shared-mem  │
              │ MCP Server  │
              │             │
              │ • Obsidian  │
              │ • FTS5      │
              │ • sqlite-vec│
              │ • embeddings│
              └─────────────┘
```

## Features

- **MCP server** with 8 tools: write, read, search, list, delete, recent, graph, reindex
- **Obsidian vault** — open in Obsidian to browse/edit visually
- **Hybrid search** — full-text (FTS5) + semantic vector (sqlite-vec + sentence-transformers)
- **Wikilinks** — `[[Note Name]]` graph between notes
- **Agent attribution** — each note tracks which agent wrote it
- **CLI utility** `mem` for shell access
- **File-based** — no external DB server needed, just SQLite

## Quick Start

### 1. Install dependencies

```bash
pip install mcp sentence-transformers sqlite-vec pyyaml
```

### 2. Clone and setup

```bash
git clone https://github.com/YOUR_USERNAME/shared-memory.git ~/shared-memory
cd ~/shared-memory
python3 mem.py reindex   # build search index from vault
```

### 3. Test

```bash
# Read a note
python3 mem.py read knowledge/esrm

# Search
python3 mem.py search "пассивное охлаждение"

# Write a note (content from stdin)
echo "Новый факт о PCA9685" | python3 mem.py write knowledge/pca9685 --tags "hardware,servo" --agent Hermes

# List notes
python3 mem.py list --category knowledge
```

### 4. Connect agents (see below)

## MCP Tools

| Tool | Description |
|---|---|
| `memory_write` | Create or update a note |
| `memory_read` | Read a note by ID |
| `memory_search` | Hybrid search (FTS5 + vector) |
| `memory_list` | List notes (filter by category, type, tag, agent) |
| `memory_delete` | Delete a note |
| `memory_recent` | Recently updated notes |
| `memory_graph` | Wikilinks graph (for a note or full vault) |
| `memory_reindex` | Rebuild search index |

## Note Format

Notes are standard markdown with YAML frontmatter, fully compatible with Obsidian:

```markdown
---
title: Note Title
type: fact|decision|task|knowledge|journal
tags: [tag1, tag2]
created: 2026-04-14T10:00:00
updated: 2026-04-14T10:30:00
updated_by: Hermes
confidence: high|medium|low
status: active|archived|deprecated
---

# Note Title

Content here. Link to [[Another Note]].
```

### Frontmatter Fields

| Field | Required | Description |
|---|---|---|
| `title` | auto | Note title (auto-generated from slug if omitted) |
| `type` | optional | fact, decision, task, knowledge, journal |
| `tags` | optional | List of tags |
| `created` | auto | ISO timestamp, set on first write |
| `updated` | auto | ISO timestamp, updated on every write |
| `updated_by` | recommended | Agent name that wrote this |
| `confidence` | optional | high, medium, low |
| `status` | optional | active (default), archived, deprecated |

### Categories (vault subdirectories)

| Directory | Purpose |
|---|---|
| `knowledge/` | Facts, reference info, project context |
| `decisions/` | Architectural and design decisions with reasoning |
| `tasks/` | Tasks (cross-agent task tracking) |
| `journal/` | Daily logs, event chronicles |
| `agents/` | Agent profiles and capabilities |

### Wikilinks

Use `[[Note Name]]` in body text to create links between notes. The `memory_graph` tool resolves these into a knowledge graph.

## Agent Configuration

### Hermes (~/.hermes/config.yaml)

```yaml
mcp:
  servers:
    shared-memory:
      command: python3
      args:
        - "/home/YOUR_USER/shared-memory/server.py"
      env:
        SHARED_MEMORY_VAULT: "/home/YOUR_USER/shared-memory/vault"
        SHARED_MEMORY_DB: "/home/YOUR_USER/shared-memory/db/memory.db"
```

After editing, restart Hermes. Tools `memory_write`, `memory_read`, etc. become available.

### QwenCode (~/.qwen/settings.json)

```json
{
  "mcpServers": {
    "shared-memory": {
      "command": "python3",
      "args": ["/home/YOUR_USER/shared-memory/server.py"],
      "transport": "stdio",
      "env": {
        "SHARED_MEMORY_VAULT": "/home/YOUR_USER/shared-memory/vault",
        "SHARED_MEMORY_DB": "/home/YOUR_USER/shared-memory/db/memory.db"
      }
    }
  }
}
```

Or via CLI:

```bash
qwen mcp add shared-memory --transport stdio \
  --command "python3 /home/YOUR_USER/shared-memory/server.py" \
  --env SHARED_MEMORY_VAULT=/home/YOUR_USER/shared-memory/vault
```

### OpenClaw (~/.openclaw/openclaw.json)

OpenClaw supports MCP servers via its plugin system or via the `openclaw mcp` command:

```bash
openclaw mcp set shared-memory --transport stdio \
  --command "python3 /home/YOUR_USER/shared-memory/server.py"
```

Or in config:

```json
{
  "mcpServers": {
    "shared-memory": {
      "command": "python3",
      "args": ["/home/YOUR_USER/shared-memory/server.py"],
      "transport": "stdio",
      "env": {
        "SHARED_MEMORY_VAULT": "/home/YOUR_USER/shared-memory/vault",
        "SHARED_MEMORY_DB": "/home/YOUR_USER/shared-memory/db/memory.db"
      }
    }
  }
}
```

### SSE Mode (for remote/shared access)

If agents are on different machines, run the server in SSE mode:

```bash
python3 ~/shared-memory/server.py --transport sse --port 8765
```

Then configure agents to connect via HTTP:

```json
{
  "mcpServers": {
    "shared-memory": {
      "url": "http://YOUR_SERVER:8765/sse",
      "transport": "sse"
    }
  }
}
```

## CLI Usage (mem)

The `mem` CLI is useful for scripting, cron jobs, and agents without MCP support.

```bash
# Setup
ln -sf ~/shared-memory/mem.py ~/.local/bin/mem

# Write
echo "Content" | mem write knowledge/topic --tags "a,b" --agent Hermes --type fact

# Read
mem read knowledge/topic

# Search
mem search "query" --mode hybrid --limit 10

# List
mem list --category knowledge --tag hardware

# Recent
mem recent --limit 5

# Graph
mem graph                    # full vault graph
mem graph knowledge/esrm     # links for specific note

# Reindex
mem reindex
```

## Environment Variables

| Variable | Default | Description |
|---|---|---|
| `SHARED_MEMORY_VAULT` | `~/shared-memory/vault` | Path to Obsidian vault |
| `SHARED_MEMORY_DB` | `~/shared-memory/db/memory.db` | Path to SQLite index |
| `SHARED_MEMORY_EMBED_MODEL` | `all-MiniLM-L6-v2` | sentence-transformers model |

## Sync Between Machines

Since the vault is just files, you can sync it with Git:

```bash
cd ~/shared-memory
git init
git remote add origin YOUR_REPO
git add -A
git commit -m "init"
git push

# Auto-sync (add to crontab)
*/5 * * * * cd ~/shared-memory && git pull --rebase && git add -A && git commit -m "sync $(date +%Y%m%d-%H%M)" && git push
```

Or use Syncthing / rsync for real-time sync.

## Project Structure

```
shared-memory/
├── server.py          # MCP server (main)
├── mem.py             # CLI utility
├── requirements.txt   # Python dependencies
├── README.md          # This file
├── LICENSE            # MIT
├── .gitignore
├── vault/             # Obsidian vault (the actual memory)
│   ├── knowledge/     # Facts and reference
│   ├── decisions/     # Design decisions
│   ├── tasks/         # Cross-agent tasks
│   ├── journal/       # Daily logs
│   └── agents/        # Agent profiles
└── db/                # SQLite search index (gitignored)
    └── memory.db
```

## Requirements

- Python 3.10+
- `mcp` — MCP protocol server
- `sentence-transformers` — embeddings (all-MiniLM-L6-v2, ~90MB)
- `sqlite-vec` — vector search in SQLite
- `pyyaml` — YAML frontmatter parsing
- `numpy`, `torch` — transitive (from sentence-transformers)

## License

MIT
