"""Read-only Codex and Hermes adapters for the local report pipeline."""

from collections import Counter
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
from pathlib import Path
import sqlite3

import project_analyzer as base


def _session_counts(sessions):
    events = [event for session in sessions for event in session["events"]]
    tools = [event for event in events if event["kind"] == "tool_call"]
    users = [event for event in events if event["kind"] == "message" and event["role"] == "user"]
    touched = sorted({p for event in tools for p in event.get("paths", [])})
    return {"events": len(events), "user_messages": len(users), "tool_calls": len(tools),
            "tool_results": sum(e["kind"] == "tool_result" for e in events),
            "test_like_calls": sum(bool(e.get("test_like")) for e in tools),
            "steering_candidates": sum(bool(base.STEERING_PATTERN.search(e["text"])) for e in users),
            "touched_paths": len(touched), "commits_in_window": 0}, touched


def collect_codex(days, codex_dir):
    root = Path(codex_dir).expanduser()
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    sessions = []
    files = list(root.rglob("*.jsonl")) if root.is_dir() else []
    for path in files:
        rows = list(base.read_rows(path))
        cwd, sid, parent = base.session_identity("codex", rows, path)
        session = base.normalize_session("codex", path, rows, cwd, "codex:" + sid,
                                         "codex:" + parent if parent else None, cutoff)
        if session:
            sessions.append(session)
    sessions = base.merge_session_segments(sessions)
    return sessions, len(files)


def _hermes_content(value):
    if not value:
        return ""
    if not isinstance(value, str):
        return str(value)
    try:
        parsed = json.loads(value)
    except (ValueError, TypeError):
        return value
    if isinstance(parsed, list):
        return base.blocks_text(parsed) or value
    if isinstance(parsed, dict):
        return str(parsed.get("text") or parsed.get("content") or value)
    return value


def collect_hermes(days, hermes_db):
    """Read coding sessions from Hermes's canonical SQLite without exporting it."""
    db = Path(hermes_db).expanduser()
    if not db.is_file():
        return [], 0
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).timestamp()
    uri = "file:" + str(db.resolve()) + "?mode=ro"
    sessions = []
    try:
        connection = sqlite3.connect(uri, uri=True, timeout=5)
        connection.row_factory = sqlite3.Row
        metadata = connection.execute(
            "SELECT id, cwd, git_repo_root, parent_session_id, started_at, last_activity_at "
            "FROM sessions WHERE source IN ('cli', 'oneshot', 'acp') "
            "AND COALESCE(NULLIF(cwd, ''), NULLIF(git_repo_root, '')) IS NOT NULL "
            "AND COALESCE(last_activity_at, started_at) >= ?", (cutoff,)).fetchall()
        for row in metadata:
            sid = "hermes:" + sha256(row["id"].encode()).hexdigest()[:16]
            cwd = row["cwd"] or row["git_repo_root"]
            # Hermes parent_session_id denotes compression lineage, not a
            # delegated coding subagent. Keep it out of episode parent links.
            messages = connection.execute(
                "SELECT id, role, content, tool_calls, tool_name, tool_call_id, timestamp "
                "FROM messages WHERE session_id = ? AND COALESCE(active, 1) = 1 "
                "ORDER BY COALESCE(display_order, id), id", (row["id"],)).fetchall()
            events = []
            for message in messages:
                stamp = message["timestamp"] or row["started_at"]
                at = base.iso(datetime.fromtimestamp(float(stamp), timezone.utc)) if stamp else None
                if not at or base.parse_time(at).timestamp() < cutoff:
                    continue
                def add(kind, **fields):
                    events.append({"id": f"{sid}:E{len(events)+1:06d}", "at": at,
                                   "kind": kind, **fields})
                role = message["role"]
                content = _hermes_content(message["content"])
                if role in ("user", "assistant") and content:
                    add("message", role=role, text=base.redact(content, 12000))
                if message["tool_calls"]:
                    try:
                        calls = json.loads(message["tool_calls"])
                    except (ValueError, TypeError):
                        calls = []
                    if isinstance(calls, list):
                        for call in calls:
                            if not isinstance(call, dict):
                                continue
                            function = call.get("function") or {}
                            name = str(function.get("name") or call.get("name") or "tool")
                            command = base.command_from_input(function.get("arguments") or call.get("input"))
                            add("tool_call", tool=name, command=base.redact(command, 1800),
                                paths=base.paths_from_input(function.get("arguments"), command),
                                call_id=str(call.get("id") or ""),
                                test_like=bool(base.TEST_PATTERN.search(command)))
                if role == "tool" and content:
                    add("tool_result", tool=message["tool_name"] or "tool",
                        call_id=message["tool_call_id"] or "", text=base.redact(content, 1200))
            if not events:
                continue
            events.sort(key=lambda event: (event["at"], event["id"]))
            sessions.append({"id": sid, "source": "hermes", "cwd": cwd,
                             "parent_id": None, "started_at": events[0]["at"],
                             "ended_at": events[-1]["at"], "event_count": len(events),
                             "source_file_count": 1, "empty_transcript": False, "events": events})
    except sqlite3.Error as error:
        raise RuntimeError("Cannot read Hermes session database") from error
    finally:
        if "connection" in locals():
            connection.close()
    return sessions, len(metadata)


def collect_all_local(days, codex_dir, claude_dir, hermes_db):
    claude = base.collect_all_claude(days, claude_dir)
    codex, codex_files = collect_codex(days, codex_dir)
    hermes, hermes_records = collect_hermes(days, hermes_db)
    sessions = claude["sessions"] + codex + hermes
    sessions.sort(key=lambda session: session["started_at"])
    stats, touched = _session_counts(sessions)
    coverage = {"files_scanned": claude["coverage"]["files_scanned"] + codex_files + hermes_records,
                "matched_files": len(sessions), "matched_sessions": len(sessions),
                "sources": dict(Counter(session["source"] for session in sessions)),
                "main_sessions": sum(not session.get("parent_id") for session in sessions),
                "subagent_sessions": sum(bool(session.get("parent_id")) for session in sessions),
                "empty_transcripts": sum(bool(session.get("empty_transcript")) for session in sessions),
                **stats}
    return {"schema": "project-evidence.v1", "project_name": "All local coding agents",
            "project_path": "multiple local sources", "generated_at": base.iso(datetime.now(timezone.utc)),
            "window_days": days, "coverage": coverage, "sessions": sessions,
            "touched_paths": touched, "commits": []}


def collect_project(project, days, codex_dir, claude_dir, hermes_db):
    project = base.canonical_project(project)
    hermes, _ = collect_hermes(days, hermes_db)
    selected = [session for session in hermes if base.project_matches(session["cwd"], project)]
    return base.collect(project, days, codex_dir, claude_dir, extra_sessions=selected)
