#!/usr/bin/env python3
"""
mem — CLI utility for shared memory vault access.

Usage:
    mem write <category/slug> [--title "Title"] [--tags a,b] [--type fact] [--agent Hermes] < content.md
    mem read <category/slug>
    mem search <query> [--limit 10] [--mode hybrid|fts|vector]
    mem list [--category knowledge] [--type fact] [--tag tag] [--agent Hermes]
    mem delete <category/slug>
    mem recent [--limit 10]
    mem graph [category/slug]
    mem reindex
"""

import sys
import os
import argparse
import json

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from server import (
    init_vault, init_db, _write, _read, search_fts, search_vector,
    list_all_notes, vault_path, extract_wikilinks, parse_frontmatter,
    reindex_all, get_db, _db_lock
)


def cmd_write(args):
    content = sys.stdin.read().strip()
    if not content:
        print("Error: no content on stdin", file=sys.stderr)
        sys.exit(1)
    import asyncio
    fpath = asyncio.run(_write(
        note_id=args.note_id,
        content=content,
        title=args.title,
        tags=args.tags.split(",") if args.tags else None,
        note_type=args.type,
        confidence=args.confidence,
        agent=args.agent,
        status=args.status,
    ))
    print(f"Written: {fpath}")


def cmd_read(args):
    import asyncio
    content = asyncio.run(_read(args.note_id))
    if content is None:
        print(f"Note not found: {args.note_id}", file=sys.stderr)
        sys.exit(1)
    print(content)


def cmd_search(args):
    results = []
    if args.mode in ("hybrid", "fts"):
        results.extend(search_fts(args.query, args.limit))
    if args.mode in ("hybrid", "vector"):
        vec_results = search_vector(args.query, args.limit)
        seen = {r["id"] for r in results}
        for vr in vec_results:
            if vr["id"] not in seen:
                results.append(vr)

    if getattr(args, "json", False):
        # Machine-readable JSON output
        print(json.dumps(results[:args.limit], ensure_ascii=False))
        return

    if not results:
        print("No results found.")
        return

    print(f"Found {len(results)} note(s):\n")
    for r in results[:args.limit]:
        print(f"  [{r['id']}] {r.get('title', '')} "
              f"by {r.get('updated_by', '?')} "
              f"tags={r.get('tags', [])}")
        if r.get("snippet"):
            print(f"    >> {r['snippet']}")
        if r.get("distance") is not None:
            print(f"    distance: {r['distance']}")


def cmd_list(args):
    notes = list_all_notes()
    if args.category:
        notes = [n for n in notes if n["id"].startswith(args.category + "/")]
    if args.type:
        notes = [n for n in notes if n.get("type") == args.type]
    if args.tag:
        notes = [n for n in notes if args.tag in n.get("tags", [])]
    if args.agent:
        notes = [n for n in notes if n.get("updated_by") == args.agent]
    notes.sort(key=lambda n: n.get("updated", ""), reverse=True)
    notes = notes[:args.limit]

    if not notes:
        print("No notes found.")
        return

    print(f"{len(notes)} note(s):\n")
    for n in notes:
        print(f"  [{n['id']}] {n.get('title', n['id'])} "
              f"({n.get('type', '')}) by {n.get('updated_by', '?')} "
              f"tags={n.get('tags', [])}")


def cmd_delete(args):
    fpath = vault_path(args.note_id)
    if not os.path.exists(fpath):
        print(f"Note not found: {args.note_id}", file=sys.stderr)
        sys.exit(1)
    os.remove(fpath)
    # Clean up search index (mirrors server.py memory_delete logic)
    conn = get_db()
    with _db_lock:
        conn.execute("DELETE FROM notes WHERE note_id = ?", (args.note_id,))
        conn.execute("DELETE FROM notes_fts WHERE note_id = ?", (args.note_id,))
        try:
            conn.execute("DELETE FROM notes_vec WHERE note_id = ?", (args.note_id,))
        except Exception:
            pass
        conn.commit()
    conn.close()
    print(f"Deleted: {args.note_id}")


def cmd_recent(args):
    notes = list_all_notes()
    notes.sort(key=lambda n: n.get("updated", ""), reverse=True)
    notes = notes[:args.limit]
    for n in notes:
        print(f"  [{n['id']}] {n.get('title', n['id'])} "
              f"by {n.get('updated_by', '?')} at {n.get('updated', '')}")


def cmd_graph(args):
    all_notes = list_all_notes()
    if args.note_id:
        fpath = vault_path(args.note_id)
        if not os.path.exists(fpath):
            print(f"Note not found: {args.note_id}", file=sys.stderr)
            sys.exit(1)
        with open(fpath, encoding="utf-8") as f:
            content = f.read()
        _, body = parse_frontmatter(content)
        links = extract_wikilinks(body)
        backlinks = []
        slug = args.note_id.split("/")[-1]
        for n in all_notes:
            if n["id"] == args.note_id:
                continue
            with open(n["path"], encoding="utf-8") as f:
                c = f.read()
            _, b = parse_frontmatter(c)
            if slug in extract_wikilinks(b) or args.note_id in extract_wikilinks(b):
                backlinks.append(n["id"])
        print(f"Links from [{args.note_id}]:")
        for l in links:
            print(f"  -> {l}")
        print(f"\nBacklinks to [{args.note_id}]:")
        for bl in backlinks:
            print(f"  <- {bl}")
    else:
        edges = []
        for n in all_notes:
            with open(n["path"], encoding="utf-8") as f:
                content = f.read()
            _, body = parse_frontmatter(content)
            for link in extract_wikilinks(body):
                edges.append((n["id"], link))
        print(f"Knowledge graph: {len(all_notes)} nodes, {len(edges)} edges\n")
        for src, dst in edges:
            print(f"  {src} -> {dst}")


def cmd_reindex(args):
    count = reindex_all()
    print(f"Reindexed {count} notes.")


def main():
    init_vault()
    init_db()

    parser = argparse.ArgumentParser(prog="mem", description="Shared memory vault CLI")
    sub = parser.add_subparsers(dest="command")

    # write
    p_write = sub.add_parser("write", help="Create or update a note")
    p_write.add_argument("note_id", help="category/slug")
    p_write.add_argument("--title", help="Note title")
    p_write.add_argument("--tags", help="Comma-separated tags")
    p_write.add_argument("--type", choices=["fact", "decision", "task", "knowledge", "journal"])
    p_write.add_argument("--confidence", choices=["high", "medium", "low"])
    p_write.add_argument("--agent", help="Agent name")
    p_write.add_argument("--status", choices=["active", "archived", "deprecated"], default="active")

    # read
    p_read = sub.add_parser("read", help="Read a note")
    p_read.add_argument("note_id")

    # search
    p_search = sub.add_parser("search", help="Search notes")
    p_search.add_argument("query")
    p_search.add_argument("--limit", type=int, default=10)
    p_search.add_argument("--mode", choices=["hybrid", "fts", "vector"], default="hybrid")
    p_search.add_argument("--json", action="store_true", help="Output as JSON (for machine consumption)")

    # list
    p_list = sub.add_parser("list", help="List notes")
    p_list.add_argument("--category")
    p_list.add_argument("--type")
    p_list.add_argument("--tag")
    p_list.add_argument("--agent")
    p_list.add_argument("--limit", type=int, default=20)

    # delete
    p_del = sub.add_parser("delete", help="Delete a note")
    p_del.add_argument("note_id")

    # recent
    p_recent = sub.add_parser("recent", help="Recent notes")
    p_recent.add_argument("--limit", type=int, default=10)

    # graph
    p_graph = sub.add_parser("graph", help="Knowledge graph / wikilinks")
    p_graph.add_argument("note_id", nargs="?", help="Note ID (omit for full graph)")

    # reindex
    sub.add_parser("reindex", help="Reindex all notes")

    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        sys.exit(1)

    commands = {
        "write": cmd_write,
        "read": cmd_read,
        "search": cmd_search,
        "list": cmd_list,
        "delete": cmd_delete,
        "recent": cmd_recent,
        "graph": cmd_graph,
        "reindex": cmd_reindex,
    }
    commands[args.command](args)


if __name__ == "__main__":
    main()
