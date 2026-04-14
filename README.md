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

## Prerequisites

### System requirements

- **Python 3.10+** (tested on 3.13)
- **pip** (Python package manager)
- **Git** (for versioning and sync)
- **Obsidian** (optional, for visual browsing/editing)
- **sqlite3 CLI** (optional, for debugging the index DB)
- **jq** (optional, for scripting JSON output)

### Install system packages

**Debian/Ubuntu:**
```bash
sudo apt install -y python3 python3-pip git sqlite3 jq
```

**macOS:**
```bash
brew install python3 git sqlite jq
```

**Windows (WSL2):**
```bash
sudo apt install -y python3 python3-pip git sqlite3 jq
# Obsidian — install on Windows side:
winget install Obsidian.Obsidian
```

### Install Python dependencies

```bash
pip install --user --break-system-packages mcp sentence-transformers sqlite-vec pyyaml numpy
```

Or in a virtual environment:
```bash
python3 -m venv .venv
source .venv/bin/activate
pip install mcp sentence-transformers sqlite-vec pyyaml numpy
```

> **Note:** `sentence-transformers` downloads the `all-MiniLM-L6-v2` model (~90MB) on first use. It is cached automatically in `~/.cache/torch/sentence_transformers/`.

> **Note:** On Debian 13+ (Trixie) pip requires `--break-system-packages` flag or use `--user`. If you use a venv, omit the flag.

### Verify installation

```bash
python3 -c "import mcp; print('mcp: OK')"
python3 -c "import sentence_transformers; print('sentence-transformers: OK')"
python3 -c "import sqlite_vec; print('sqlite-vec: OK')"
python3 -c "import yaml; print('pyyaml: OK')"
python3 -c "import numpy; print('numpy: OK')"
sqlite3 --version
```

All should print OK without errors.

## Quick Start

### 1. Clone and setup

```bash
git clone https://github.com/hzname/shared-memory.git ~/shared-memory
cd ~/shared-memory
python3 mem.py reindex   # build search index from vault
```

### 2. Test via CLI

```bash
# Read a note
python3 mem.py read knowledge/esrm

# Search
python3 mem.py search "пассивное охлаждение"

# Write a note (content from stdin)
echo "Новый факт о PCA9685" | python3 mem.py write knowledge/pca9685 --tags "hardware,servo" --agent Hermes

# List notes
python3 mem.py list --category knowledge

# Recent changes
python3 mem.py recent
```

### 3. Test MCP server directly

```bash
# Stdio mode (used by agents)
echo '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"test","version":"1.0"}}}' | python3 server.py
```

### 4. Connect agents

See [Agent Configuration](#agent-configuration) below.

### 5. Open in Obsidian (optional)

1. Launch Obsidian
2. "Open folder as vault"
3. Select the `vault/` directory:
   - Linux: `~/shared-memory/vault`
   - Windows/WSL: copy vault to Windows side first (Obsidian cannot open UNC paths `\\wsl.localhost\...`)

```bash
# Windows/WSL — make vault accessible to Obsidian
mkdir -p /mnt/c/Users/$USER/ObsidianVaults/shared-memory
cp -r ~/shared-memory/vault/* /mnt/c/Users/$USER/ObsidianVaults/shared-memory/
# Then point SHARED_MEMORY_VAULT to the Windows path in agent config
```

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
| `tasks/` | Tasks with attempt history and success tracking |
| `journal/` | Session logs, daily chronicles |
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

After editing, restart Hermes session. Tools `memory_write`, `memory_read`, etc. become available automatically.

**WSL + Obsidian** — if vault is on Windows side:
```yaml
env:
  SHARED_MEMORY_VAULT: "/mnt/c/Users/YOUR_USER/ObsidianVaults/shared-memory"
  SHARED_MEMORY_DB: "/home/YOUR_USER/shared-memory/db/memory.db"
```

### QwenCode

Via CLI:
```bash
qwen mcp add shared-memory --transport stdio \
  --command "python3 /home/YOUR_USER/shared-memory/server.py" \
  --env SHARED_MEMORY_VAULT=/home/YOUR_USER/shared-memory/vault
```

Or in `~/.qwen/settings.json`:
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

### OpenClaw

Via CLI:
```bash
openclaw mcp set shared-memory --transport stdio \
  --command "python3 /home/YOUR_USER/shared-memory/server.py"
```

Or in `~/.openclaw/openclaw.json`:
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

### SSE Mode (for remote agents)

If agents are on different machines, run the server in SSE mode:

```bash
python3 ~/shared-memory/server.py --transport sse --port 8765
```

Then configure agents:
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

### Agent Usage Guidelines

Agents should follow these conventions when working with shared memory:

**Reading:**
- Use `memory_recent` at session start to recall context
- Use `memory_search` to find relevant knowledge before asking the user
- Use `memory_graph` to discover related notes via wikilinks

**Writing:**
- Always set `agent` field to your name (Hermes, OpenClaw, QwenCode)
- Use correct `type`: `fact` for objective info, `decision` for choices made, `task` for active work items, `journal` for session logs
- Set `confidence` when uncertain (`low`/`medium`)
- Use `[[wikilinks]]` to connect related notes
- Add relevant `tags` for filtering

**Conventions:**
- Note ID format: `category/slug` (e.g. `knowledge/esrm`, `decisions/2026-04-14-cooling`)
- Slug: lowercase, hyphens instead of spaces
- Don't delete notes created by other agents — use `status: deprecated` instead
- Run `memory_reindex` after manual vault edits outside of MCP

**Action Logging (Variant C):**

For tasks with multiple attempts, create a task note and update it with each attempt:

```markdown
---
type: task
tags: [project, component]
status: blocked|in_progress|completed|cancelled
confidence: high|medium|low
success_rate: 0.0-1.0
agent: Hermes
---

# Task Title

## Goal
What needs to be done

## Attempt History
| # | When | Agent | Approach | Score | Time | Note |
|---|---|---|---|---|---|---|
| 1 | 04-14 12:30 | Hermes | docker exec -d | 0 | 5min | Zombie process |
| 2 | 04-14 13:15 | Hermes | manual exec | 5 | 2min | Works! |

## Root Cause (if found)
Description

## Lessons Learned
- What was learned

## Next Step
Specific action to try
```

Success scale: 5=full success, 4=success with caveats, 3=partial, 2=fail+lesson, 1=fail+unclear, 0=regression.

`success_rate` = sum of scores / (attempts × 5)

## Agent Awareness Plugins

Each agent can connect to shared-memory automatically via hooks/plugins that:
1. **Recall context** before each LLM turn (`pre_llm_call`)
2. **Warn on failures** before tool execution (`pre_tool_call`)
3. **Log results** after tool execution (`post_tool_call`)

Full guide with ready-to-use plugin code: **[docs/agent-awareness-plugins.md](docs/agent-awareness-plugins.md)**

| Agent | Hook System | Inject Context | Block Tools | Log Results |
|-------|-------------|----------------|-------------|-------------|
| **Hermes** | Plugin (`ctx.register_hook`) | Yes (`pre_llm_call`) | Yes (`pre_tool_call`) | Yes (`post_tool_call`) |
| **OpenClaw** | Gateway hooks (JS) | Limited | No | Gateway events |
| **QwenCode** | MCP + skills | Via skill | No | Via skill |

Quick install for Hermes:
```bash
mkdir -p ~/.hermes/plugins/shared-memory-awareness
cp docs/plugins/hermes/{plugin.yaml,__init__.py,hooks.py} ~/.hermes/plugins/shared-memory-awareness/
hermes  # plugin loads automatically
```

## CLI Usage (mem.py)

```bash
# Install globally (optional)
ln -sf ~/shared-memory/mem.py ~/.local/bin/mem

# Write (content from stdin)
echo "Content" | mem write knowledge/topic --tags "a,b" --agent Hermes --type fact

# Read
mem read knowledge/topic

# Search (modes: hybrid, fts, vector)
mem search "query" --mode hybrid --limit 10

# List
mem list --category knowledge --tag hardware

# Recent
mem recent --limit 5

# Graph
mem graph                    # full vault graph
mem graph knowledge/esrm     # links for specific note

# Reindex (after manual vault edits)
mem reindex
```

## Environment Variables

| Variable | Default | Description |
|---|---|---|
| `SHARED_MEMORY_VAULT` | `~/shared-memory/vault` | Path to Obsidian vault |
| `SHARED_MEMORY_DB` | `~/shared-memory/db/memory.db` | Path to SQLite index |
| `SHARED_MEMORY_EMBED_MODEL` | `all-MiniLM-L6-v2` | sentence-transformers model name |

## Sync Between Machines

Since the vault is just files, sync with Git:

```bash
cd ~/shared-memory
git add -A && git commit -m "sync $(date +%Y%m%d-%H%M)" && git push

# Auto-sync via crontab (every 5 minutes)
crontab -e
# Add:
# */5 * * * * cd $HOME/shared-memory && git pull --rebase && git add -A && git commit -m "sync $(date +\%Y\%m\%d-\%H\%M)" && git push
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
│   ├── tasks/         # Tasks with attempt history
│   ├── journal/       # Session logs
│   └── agents/        # Agent profiles
└── db/                # SQLite search index (gitignored)
    └── memory.db
```

## Troubleshooting

| Problem | Solution |
|---|---|
| `ModuleNotFoundError: No module named 'mcp'` | `pip install --user --break-system-packages mcp sentence-transformers sqlite-vec pyyaml` |
| Model download hangs on first use | `sentence-transformers` downloads ~90MB model. Check internet connection. |
| Obsidian can't open vault on WSL | Copy vault to Windows path: `cp -r ~/shared-memory/vault/* /mnt/c/Users/$USER/ObsidianVaults/shared-memory/` |
| Search returns no results | Run `python3 mem.py reindex` to rebuild the index |
| `FTS5` errors in server log | SQLite needs FTS5 compiled in. On Debian: `sudo apt install sqlite3` (includes FTS5) |
| sqlite-vec not found | Vector search is optional — FTS5 works without it. To fix: `pip install sqlite-vec` |
| Permission denied on vault/ | Check `SHARED_MEMORY_VAULT` env var points to a writable directory |
| DB locked errors | Only one server process should write at a time. WAL mode is enabled by default for concurrent reads. |

## Requirements

### Python packages (pip)

| Package | Purpose | Size |
|---|---|---|
| `mcp>=1.0` | MCP protocol server | small |
| `sentence-transformers>=2.0` | Text embeddings (all-MiniLM-L6-v2) | ~90MB model |
| `sqlite-vec>=0.1` | Vector search in SQLite | small |
| `pyyaml>=6.0` | YAML frontmatter parsing | small |
| `numpy` | Array operations (transitive dep) | medium |
| `torch` | PyTorch backend (transitive, pulled by sentence-transformers) | large |

### System tools (optional but recommended)

| Tool | Purpose |
|---|---|
| `sqlite3` CLI | Debug index DB, run manual queries |
| `jq` | Parse JSON output in scripts |
| `git` | Version and sync vault between machines |
| `Obsidian` | Visual browsing/editing of vault |

## License

MIT
