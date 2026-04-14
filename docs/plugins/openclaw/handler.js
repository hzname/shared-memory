/**
 * Shared Memory Awareness — OpenClaw gateway hook.
 *
 * Events: agent:start, agent:step, agent:end
 *
 * Uses mem.py CLI to search the shared-memory vault and log session data.
 * For full context injection, agents should use MCP tools directly.
 */

import { execFile } from "node:child_process";
import { promisify } from "node:util";
import path from "node:path";
import os from "node:os";

const execFileAsync = promisify(execFile);

const MEM_CLI = process.env.SHARED_MEMORY_CLI ||
  path.join(os.homedir(), "shared-memory", "mem.py");

/**
 * FTS search via mem.py CLI.
 */
async function memSearch(query, limit = 3) {
  try {
    const { stdout } = await execFileAsync("python3", [
      MEM_CLI, "search", query, "--mode", "fts", "--limit", String(limit)
    ], { timeout: 5000 });
    const data = JSON.parse(stdout);
    return Array.isArray(data) ? data : [];
  } catch {
    return [];
  }
}

/**
 * Main hook handler — called for all subscribed events.
 */
const handler = async (event) => {
  try {
    const msg = event.context?.message || "";

    if (event.type === "agent:start" && msg.length >= 10) {
      const results = await memSearch(msg.slice(0, 200), 3);
      if (results.length > 0) {
        // OpenClaw hooks can't inject context into the agent loop directly.
        // The agent should use memory_search MCP tool for context recall.
        // This hook logs the recall for observability.
        const titles = results.map(r => r.title || r.note_id || "?").join(", ");
        console.log(`[shared-memory] Recalled ${results.length} notes for session start: ${titles}`);
      }
    }

    if (event.type === "agent:end") {
      const response = event.context?.response || "";
      console.log(`[shared-memory] Session ended. Response length: ${response.length}`);
    }
  } catch (err) {
    console.error(`[shared-memory] Hook error: ${err.message}`);
  }
};

export { handler as default };
