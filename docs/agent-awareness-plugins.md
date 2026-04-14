# Shared Memory Awareness — Agent Plugin Guide

Плагины/хуки которые подключают каждого агента к shared-memory для:
1. **Предупреждения** перед tool-call если есть история провалов
2. **Логирования** результатов tool-call в task-ноты
3. **Контекстной инъекции** релевантного опыта перед каждым LLM-ходом

---

## Общая архитектура

```
┌─────────────────────────────────────────────────┐
│  Агент (Hermes / OpenClaw / QwenCode)           │
│                                                  │
│  ┌──────────────┐  ┌──────────────┐             │
│  │ pre_llm_call │  │pre_tool_call │             │
│  │ → inject     │  │ → warn/block │             │
│  │   context    │  │   if history │             │
│  └──────────────┘  └──────────────┘             │
│                                                  │
│  ┌──────────────┐                               │
│  │post_tool_call│                               │
│  │ → log result │                               │
│  └──────┬───────┘                               │
└─────────┼───────────────────────────────────────┘
          │ MCP / subprocess
  ┌───────▼────────┐
  │  shared-memory │
  │  MCP Server    │
  └────────────────┘
```

### Что делают хуки

| Хук | Когда | Что делает |
|-----|-------|------------|
| `pre_llm_call` | Перед каждым LLM-ходом | `memory_search` по теме сессии → inject релевантный опыт в user message |
| `pre_tool_call` | Перед каждым tool-call | FTS-поиск по `tool_name` + аргументам → warn/block если есть провалы |
| `post_tool_call` | После каждого tool-call | Записывает результат в task-ноту (успех/провал + score) |

### Success Scale

| Score | Значение |
|-------|----------|
| 5 | Полный успех |
| 4 | Успех с оговорками |
| 3 | Частичный успех |
| 2 | Провал + извлечён урок |
| 1 | Провал, причина неясна |
| 0 | Регрессия (стало хуже) |

---

## 1. Hermes Plugin

Hermes поддерживает полноценную plugin-систему с хуками через `ctx.register_hook()`.
Плагины размещаются в `~/.hermes/plugins/<name>/`.

### Структура

```
~/.hermes/plugins/shared-memory-awareness/
├── plugin.yaml      # Манифест
├── __init__.py      # Регистрация хуков
└── hooks.py         # Логика хуков
```

### plugin.yaml

```yaml
name: shared-memory-awareness
version: 1.0.0
description: Inject shared-memory experience into agent decisions — context recall, tool-call warnings, result logging
provides_hooks:
  - pre_llm_call
  - post_tool_call
```

### hooks.py

```python
"""Shared Memory Awareness — hook logic for Hermes."""

import json
import logging
import os
import subprocess
import sys
from pathlib import Path

logger = logging.getLogger(__name__)

# --- Configuration ----------------------------------------------------------

# Path to shared-memory CLI
MEM_CLI = os.environ.get("SHARED_MEMORY_CLI", str(Path.home() / "shared-memory" / "mem.py"))

# Minimum relevance score (0-5) to show warnings
WARN_THRESHOLD = 1

# Max attempts to show in warning
MAX_SHOWN_ATTEMPTS = 5

# Whether to log all tool results to shared-memory task notes
LOG_RESULTS = os.environ.get("SHARED_MEMORY_LOG_RESULTS", "true").lower() == "true"

# Tools to skip (no logging, no warnings)
SKIP_TOOLS = {"memory_write", "memory_read", "memory_search", "memory_list",
              "memory_delete", "memory_recent", "memory_graph", "memory_reindex"}

# --- Helpers ----------------------------------------------------------------

def _mem_search(query: str, limit: int = 3) -> list:
    """FTS search via mem.py CLI. Returns list of dicts with title + snippet."""
    try:
        result = subprocess.run(
            [sys.executable, MEM_CLI, "search", query, "--mode", "fts",
             "--limit", str(limit), "--json"],
            capture_output=True, text=True, timeout=5
        )
        if result.returncode != 0:
            return []
        # mem.py --json outputs a JSON array of result dicts
        data = json.loads(result.stdout)
        return data if isinstance(data, list) else []
    except Exception as e:
        logger.debug("shared-memory search failed: %s", e)
        return []


def _mem_write(note_id: str, content: str, **kwargs):
    """Write to shared-memory via mem.py CLI."""
    try:
        proc = subprocess.run(
            [sys.executable, MEM_CLI, "write", note_id,
             "--agent", kwargs.get("agent", "Hermes"),
             "--type", kwargs.get("note_type", "task"),
             *(["--tags", kwargs["tags"]] if "tags" in kwargs else [])],
            input=content, capture_output=True, text=True, timeout=10
        )
        if proc.returncode != 0:
            logger.debug("shared-memory write failed: %s", proc.stderr)
    except Exception as e:
        logger.debug("shared-memory write failed: %s", e)


# --- Hooks ------------------------------------------------------------------

def recall_context(session_id, user_message, is_first_turn, **kwargs):
    """pre_llm_call: Inject relevant shared-memory context before each LLM turn."""
    if not user_message or len(user_message.strip()) < 10:
        return None

    results = _mem_search(user_message[:200], limit=3)
    if not results:
        return None

    lines = ["[SHARED MEMORY — relevant context from past sessions]"]
    for r in results[:3]:
        title = r.get("title", r.get("note_id", "?"))
        snippet = r.get("snippet", "")[:200]
        lines.append(f"- {title}: {snippet}")

    return {"context": "\n".join(lines)}


def warn_on_tool(tool_name, args, task_id, session_id, **kwargs):
    """pre_tool_call: Warn if tool+args have history of failures.

    Returns {"action": "block", "message": "..."} if the tool should be blocked,
    or logs a warning. Does NOT block by default — only injects awareness.
    """
    if tool_name in SKIP_TOOLS:
        return None

    # Build search query from tool name + first arg value
    query_parts = [tool_name]
    if isinstance(args, dict):
        for v in list(args.values())[:2]:
            if isinstance(v, str) and len(v) > 3:
                query_parts.append(v[:80])

    results = _mem_search(" ".join(query_parts), limit=3)
    if not results:
        return None

    # Check for low-scoring attempts in results
    failures = []
    for r in results:
        snippet = r.get("snippet", "").lower()
        # Look for score indicators in task notes
        if any(w in snippet for w in ["score: 0", "score: 1", "провал", "failed", "fail"]):
            failures.append(r)

    if not failures:
        return None

    # Log warning (don't block — just warn)
    logger.info(
        "shared-memory: tool %s has %d failed attempts in history for %s",
        tool_name, len(failures), " ".join(query_parts)
    )
    # Return None = don't block, just observe
    # To block: return {"action": "block", "message": "..."}
    return None


def log_tool_result(tool_name, args, result, task_id, session_id, **kwargs):
    """post_tool_call: Log tool result to shared-memory for future reference.

    Only logs terminal commands and significant operations.
    Skips read-only and memory tools.
    """
    if not LOG_RESULTS or tool_name in SKIP_TOOLS:
        return

    # Only log terminal commands (most actionable for future reference)
    if tool_name != "terminal":
        return

    if not isinstance(args, dict):
        return

    command = args.get("command", "")
    if not command or len(command) < 5:
        return

    # Determine success from result
    success = True
    score = 5
    result_text = ""
    try:
        parsed = json.loads(result) if isinstance(result, str) else result
        exit_code = parsed.get("exit_code", 0) if isinstance(parsed, dict) else 0
        success = exit_code == 0
        result_text = str(parsed.get("output", ""))[:200] if isinstance(parsed, dict) else ""
        if not success:
            score = 1
    except (json.JSONDecodeError, TypeError):
        pass

    # Create a brief log entry (don't write full task note — just update existing)
    # The actual task note management is left to the agent via memory_write tool
    logger.info(
        "shared-memory: tool %s result: success=%s score=%d cmd=%s",
        tool_name, success, score, command[:80]
    )
```

### __init__.py

```python
"""Shared Memory Awareness plugin for Hermes."""

import logging

from . import hooks

logger = logging.getLogger(__name__)


def register(ctx):
    """Register shared-memory awareness hooks."""
    ctx.register_hook("pre_llm_call", hooks.recall_context)
    ctx.register_hook("pre_tool_call", hooks.warn_on_tool)
    ctx.register_hook("post_tool_call", hooks.log_tool_result)

    logger.info("shared-memory-awareness: registered 3 hooks")
```

### Установка

```bash
# Создать директорию плагина
mkdir -p ~/.hermes/plugins/shared-memory-awareness

# Скопировать 3 файла (plugin.yaml, __init__.py, hooks.py)
# ИЛИ создать симлинк из репозитория:
cp /path/to/shared-memory/docs/plugins/hermes/* ~/.hermes/plugins/shared-memory-awareness/

# Перезапустить Hermes — плагин загрузится автоматически
hermes
```

Проверка:
```
/plugins
# Должно показать:
# ✓ shared-memory-awareness v1.0.0 (0 tools, 3 hooks)
```

---

## 2. OpenClaw Hook

OpenClaw использует gateway hooks (JS handlers + HOOK.md manifest).
Hooks размещаются в `~/.openclaw/hooks/` и регистрируются автоматически.

### Структура

```
~/.openclaw/hooks/shared-memory-awareness/
├── HOOK.md        # Манифест (frontmatter + документация)
└── handler.js     # ESM handler
```

### HOOK.md

```markdown
---
name: shared-memory-awareness
description: "Inject shared-memory experience into agent decisions"
homepage: https://github.com/hzname/shared-memory
metadata:
  {
    "openclaw":
      {
        "emoji": "🧠",
        "events": ["agent:start", "agent:step", "agent:end"],
        "requires": {},
      },
  }
---

# Shared Memory Awareness

Recalls relevant context from shared-memory vault and injects it into the agent's
decision-making process.

## What It Does

1. **agent:start** — Searches shared-memory for context relevant to the user's message
2. **agent:step** — Warns if current tool has history of failures
3. **agent:end** — Logs session outcome to shared-memory

## Requirements

- `python3` with `~/shared-memory/mem.py` accessible
- Shared-memory MCP server configured and running
```

### handler.js

```javascript
import { createSubsystemLogger } from "openclaw"; // bundled import path
import { execFile } from "node:child_process";
import { promisify } from "node:util";
import path from "node:path";
import os from "node:os";

const execFileAsync = promisify(execFile);
const log = createSubsystemLogger("shared-memory-awareness");

const MEM_CLI = process.env.SHARED_MEMORY_CLI ||
  path.join(os.homedir(), "shared-memory", "mem.py");

async function memSearch(query, limit = 3) {
  try {
    const { stdout } = await execFileAsync("python3", [
      MEM_CLI, "search", query, "--mode", "fts", "--limit", String(limit)
    ], { timeout: 5000 });
    return JSON.parse(stdout);
  } catch (err) {
    log.debug(`mem search failed: ${err.message}`);
    return [];
  }
}

async function memWrite(noteId, content, agent = "OpenClaw") {
  try {
    await execFileAsync("python3", [
      MEM_CLI, "write", noteId, "--agent", agent, "--type", "task"
    ], { input: content, timeout: 10000 });
  } catch (err) {
    log.debug(`mem write failed: ${err.message}`);
  }
}

/**
 * Main hook handler — called for all subscribed events.
 */
const handler = async (event) => {
  try {
    if (event.type === "agent:start") {
      // Recall context from shared-memory based on user message
      const message = event.context?.message || "";
      if (message.length < 10) return;

      const results = await memSearch(message.slice(0, 200), 3);
      if (results.length === 0) return;

      log.info(`Recalled ${results.length} memories for session start`);
      // OpenClaw hooks can't inject context directly into the agent loop,
      // but they can write to a session-scoped file that BOOT.md or skills read.
    }

    if (event.type === "agent:step") {
      // Log step info for later analysis
      const tools = event.context?.tool_names || [];
      const iteration = event.context?.iteration || 0;
      log.debug(`Step ${iteration}, tools: ${tools.join(", ")}`);
    }

    if (event.type === "agent:end") {
      // Log session completion
      const message = event.context?.message || "";
      const response = event.context?.response || "";
      log.info("Session completed");
    }
  } catch (err) {
    log.error(`Handler error: ${err.message}`);
  }
};

export { handler as default };
```

### Установка

```bash
# Создать директорию хука
mkdir -p ~/.openclaw/hooks/shared-memory-awareness

# Скопировать файлы
cp /path/to/shared-memory/docs/plugins/openclaw/HOOK.md ~/.openclaw/hooks/shared-memory-awareness/
cp /path/to/shared-memory/docs/plugins/openclaw/handler.js ~/.openclaw/hooks/shared-memory-awareness/

# Проверить
openclaw hooks list
# Должно показать:
# ✓ ready  🧠 shared-memory-awareness
```

> **Примечание:** OpenClaw hooks работают на уровне gateway и не могут напрямую
> инжектировать контекст в agent loop. Для полной интеграции используйте
> MCP-инструменты `memory_search`/`memory_write` из skill-файлов агента.

---

## 3. QwenCode MCP + Skills

QwenCode не имеет развитой hook-системы (hooks list пуст).
Интеграция через **MCP-инструменты** + **skill-файлы**.

### Шаг 1: Подключить MCP-сервер

```bash
qwen mcp add shared-memory -s user \
  -e SHARED_MEMORY_VAULT=/home/sg/shared-memory/vault \
  -e SHARED_MEMORY_DB=/home/sg/shared-memory/db/memory.db \
  --trust \
  -- python3 /home/sg/shared-memory/server.py
```

### Шаг 2: Создать skill-файл

Создать `~/.qwen/skills/shared-memory.md`:

```markdown
# Shared Memory Awareness

You have access to a shared-memory vault via MCP tools: memory_search, memory_write,
memory_read, memory_list, memory_recent, memory_graph.

## Before Each Complex Action

1. `memory_search("relevant topic")` — check if there's history of successes/failures
2. If results show failed attempts — choose a different approach
3. If results show successful approach — follow the proven path

## After Completing a Task

1. If the task involved multiple attempts — create/update a task note:
   - `memory_write("tasks/descriptive-slug", content, type="task", agent="QwenCode")`
2. Include attempt history table with success scores (0-5)
3. Document root causes and lessons learned

## At Session Start

1. `memory_recent(limit=5)` — recall what was worked on recently
2. If relevant — `memory_read("knowledge/topic")` to get full context

## Scoring

| Score | Meaning |
|-------|---------|
| 5 | Full success |
| 4 | Success with caveats |
| 3 | Partial success |
| 2 | Failed + lesson learned |
| 1 | Failed, unclear cause |
| 0 | Regression |
```

### Установка

```bash
# MCP
qwen mcp add shared-memory -s user \
  -e SHARED_MEMORY_VAULT=/home/sg/shared-memory/vault \
  -e SHARED_MEMORY_DB=/home/sg/shared-memory/db/memory.db \
  --trust \
  -- python3 /home/sg/shared-memory/server.py

# Skill
cp /path/to/shared-memory/docs/plugins/qwencode/skill.md ~/.qwen/skills/shared-memory.md

# Проверить
qwen mcp list
```

---

## Сводная таблица

| Агент | Hook System | Inject Context | Block Tools | Log Results | Status |
|-------|-------------|----------------|-------------|-------------|--------|
| **Hermes** | Plugin (`ctx.register_hook`) | `pre_llm_call` → return string | `pre_tool_call` → `{"action":"block"}` | `post_tool_call` | Full support |
| **OpenClaw** | Gateway hooks (JS) | Limited (gateway-only) | No | Gateway events only | Partial (MCP + skills for full) |
| **QwenCode** | MCP + skills (no hooks) | Via skill instructions | No | Via skill instructions | MCP only |

### Приоритет интеграции

1. **Hermes** — полная поддержка через плагин (3 hooks, inject + warn + log)
2. **OpenClaw** — MCP для прямой работы + gateway hook для логирования
3. **QwenCode** — MCP + skill-файл (нет hooks API, только инструкция агенту)

---

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `SHARED_MEMORY_CLI` | `~/shared-memory/mem.py` | Path to mem.py CLI |
| `SHARED_MEMORY_LOG_RESULTS` | `true` | Enable/disable tool result logging |
| `SHARED_MEMORY_VAULT` | `~/shared-memory/vault` | Vault path (for MCP server) |
| `SHARED_MEMORY_DB` | `~/shared-memory/db/memory.db` | SQLite index path |
