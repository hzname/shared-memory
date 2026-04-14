---
name: shared-memory-awareness
description: "Inject shared-memory experience into agent decisions — recall context, log outcomes"
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

Recalls relevant context from the shared-memory vault and makes it available
to the agent during decision-making.

## What It Does

1. **agent:start** — Searches shared-memory for context relevant to the user's message
2. **agent:step** — Logs tool usage for later analysis
3. **agent:end** — Logs session outcome to shared-memory

## Requirements

- `python3` with `~/shared-memory/mem.py` accessible
- Shared-memory MCP server configured in OpenClaw
