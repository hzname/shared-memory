"""Shared Memory Awareness — hook logic for Hermes plugin.

Three hooks that connect Hermes to the shared-memory vault:

1. recall_context (pre_llm_call) — FTS search on user message, inject relevant past experience
2. warn_on_tool (pre_tool_call) — warn if tool+args have failure history
3. log_tool_result (post_tool_call) — log terminal command outcomes for future reference
"""

import json
import logging
import os
import subprocess
import sys
from pathlib import Path

logger = logging.getLogger(__name__)

# --- Configuration -----------------------------------------------------------

MEM_CLI = os.environ.get(
    "SHARED_MEMORY_CLI",
    str(Path.home() / "shared-memory" / "mem.py")
)

LOG_RESULTS = os.environ.get("SHARED_MEMORY_LOG_RESULTS", "true").lower() == "true"

# Tools that are read-only or memory-internal — skip logging and warnings
SKIP_TOOLS = frozenset({
    "memory_write", "memory_read", "memory_search", "memory_list",
    "memory_delete", "memory_recent", "memory_graph", "memory_reindex",
})


# --- Helpers -----------------------------------------------------------------

def _mem_search(query: str, limit: int = 3) -> list:
    """FTS search via mem.py CLI. Returns list of result dicts."""
    try:
        result = subprocess.run(
            [sys.executable, MEM_CLI, "search", query, "--mode", "fts",
             "--limit", str(limit), "--json"],
            capture_output=True, text=True, timeout=5
        )
        if result.returncode != 0 or not result.stdout.strip():
            return []
        data = json.loads(result.stdout)
        return data if isinstance(data, list) else []
    except (subprocess.TimeoutExpired, json.JSONDecodeError, FileNotFoundError) as e:
        logger.debug("shared-memory search failed: %s", e)
        return []


# --- Hooks -------------------------------------------------------------------

def recall_context(session_id, user_message, is_first_turn, **kwargs):
    """pre_llm_call hook: inject relevant shared-memory context.

    Searches the vault for notes related to the user's message and returns
    them as injected context. This is the only hook whose return value
    is used by Hermes — the returned string is appended to the user message.

    Returns dict with "context" key, or None.
    """
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

    text = "\n".join(lines)
    logger.info("shared-memory: injected %d results into context", len(results[:3]))
    return {"context": text}


def warn_on_tool(tool_name, args, task_id, session_id, tool_call_id="", **kwargs):
    """pre_tool_call hook: warn if tool+args have failure history.

    Searches shared-memory for past attempts with this tool and arguments.
    If failures are found, logs a warning. Can optionally block the tool
    by returning {"action": "block", "message": "..."}.

    Currently does NOT block — only observes and logs. To enable blocking,
    change the return value at the marked location.
    """
    if tool_name in SKIP_TOOLS:
        return None

    # Build search query from tool name + first arg values
    query_parts = [tool_name]
    if isinstance(args, dict):
        for v in list(args.values())[:2]:
            if isinstance(v, str) and len(v) > 3:
                query_parts.append(v[:80])

    query = " ".join(query_parts)
    results = _mem_search(query, limit=3)
    if not results:
        return None

    # Look for failure indicators in results
    failures = []
    for r in results:
        snippet = r.get("snippet", "").lower()
        if any(w in snippet for w in ["score: 0", "score: 1", "провал", "failed", "fail", "ошибка"]):
            failures.append(r)

    if not failures:
        return None

    logger.warning(
        "shared-memory: tool %s has %d failure(s) in history — query was: %s",
        tool_name, len(failures), query
    )

    # To BLOCK the tool call instead of just warning, uncomment:
    # return {"action": "block",
    #         "message": f"Tool {tool_name} has {len(failures)} past failures. See shared-memory."}

    return None  # Observer-only — don't block


def log_tool_result(tool_name, args, result, task_id, session_id, **kwargs):
    """post_tool_call hook: log tool results for future reference.

    Only logs terminal commands (most actionable for future debugging).
    Determines success/failure from the result JSON and logs it.
    The actual writing to shared-memory is left to the agent via memory_write
    — this hook only logs to the Python logger.
    """
    if not LOG_RESULTS or tool_name in SKIP_TOOLS:
        return

    # Only log terminal commands — they're the most useful for future reference
    if tool_name != "terminal":
        return

    if not isinstance(args, dict):
        return

    command = args.get("command", "")
    if not command or len(command) < 5:
        return

    # Parse result to determine success
    success = True
    score = 5
    try:
        parsed = json.loads(result) if isinstance(result, str) else result
        if isinstance(parsed, dict):
            exit_code = parsed.get("exit_code", 0)
            success = exit_code == 0
            if not success:
                score = 1
    except (json.JSONDecodeError, TypeError):
        pass

    logger.info(
        "shared-memory: terminal result — success=%s score=%d cmd='%.80s'",
        success, score, command
    )
