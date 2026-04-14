# Shared Memory Awareness

You have access to a shared-memory vault via MCP tools: `memory_search`, `memory_write`,
`memory_read`, `memory_list`, `memory_recent`, `memory_graph`.

## Before Each Complex Action

1. `memory_search("relevant topic")` — check if there's history of successes/failures
2. If results show failed attempts — choose a different approach
3. If results show successful approach — follow the proven path

## After Completing a Task with Multiple Attempts

1. Create/update a task note:
   ```
   memory_write("tasks/descriptive-slug", content, type="task", agent="QwenCode")
   ```
2. Include attempt history table with success scores:

```markdown
## Attempt History
| # | When | Agent | Approach | Score | Time | Note |
|---|------|-------|----------|-------|------|------|
| 1 | 04-14 12:30 | QwenCode | docker exec -d | 0 | 5min | Zombie process |
| 2 | 04-14 13:15 | QwenCode | manual exec | 5 | 2min | Works! |
```

3. Document root causes and lessons learned

## At Session Start

1. `memory_recent(limit=5)` — recall what was worked on recently
2. If relevant — `memory_read("knowledge/topic")` to get full context
3. `memory_search("current task keywords")` — find related tasks and decisions

## Success Scoring

When recording task results, use this scale:

| Score | Meaning |
|-------|---------|
| 5 | Full success — task completed perfectly |
| 4 | Success with caveats — minor issues |
| 3 | Partial success — some goals met |
| 2 | Failed + lesson learned — root cause identified |
| 1 | Failed, unclear cause — needs investigation |
| 0 | Regression — made things worse |

## Conventions

- Note ID format: `category/slug` (e.g. `tasks/fix-docker-autostart`)
- Always set `agent="QwenCode"` when writing
- Use `[[wikilinks]]` to connect related notes
- Don't delete other agents' notes — set `status: deprecated` instead
