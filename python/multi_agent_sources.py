# -*- coding: utf-8 -*-
"""Read-only Codex and Hermes adapters for the local report pipeline."""

from collections import Counter
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import io
import json
import os
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
                "FROM messages WHERE session_id = ? AND (COALESCE(active, 1) = 1 OR COALESCE(compacted, 0) = 1) "
                "ORDER BY COALESCE(display_order, id), id", (row["id"],)).fetchall()
            events = []
            call_refs = {}
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
                            arguments = function.get("arguments") or call.get("input")
                            if isinstance(arguments, str):
                                try:
                                    arguments = json.loads(arguments)
                                except ValueError:
                                    pass
                            command = base.command_from_input(arguments)
                            add("tool_call", tool=name, command=base.redact(command, 1800),
                                paths=base.paths_from_input(arguments, command),
                                call_id=str(call.get("id") or ""),
                                test_like=bool(base.TEST_PATTERN.search(command)))
                            if call.get("id"):
                                call_refs[str(call["id"])] = events[-1]["id"]
                if role == "tool" and content:
                    add("tool_result", tool=message["tool_name"] or "tool",
                        call_id=message["tool_call_id"] or "",
                        call_ref=call_refs.get(message["tool_call_id"] or ""),
                        text=base.redact(content, 1200))
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


def _dsh_text(content):
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(str(block.get("text", "")) for block in content
                         if isinstance(block, dict) and block.get("type") == "text")
    return ""


def _dsh_rows(path):
    try:
        if path.suffix == ".zstd":
            try:
                import zstandard
            except ImportError as error:
                raise RuntimeError("DeepSeek harness compressed logs require Python package 'zstandard'") from error
            with path.open("rb") as stream, zstandard.ZstdDecompressor().stream_reader(stream) as decoded:
                for line in io.TextIOWrapper(decoded, encoding="utf-8", errors="replace"):
                    try:
                        row = json.loads(line)
                    except ValueError:
                        continue
                    if isinstance(row, dict):
                        yield row
        else:
            yield from base.read_rows(path)
    except OSError:
        return


def collect_deepseek(days, dsh_dir):
    """Read DeepSeek harness v4 session logs without running its agent."""
    if dsh_dir is None:
        configured = os.environ.get("DSH_HOME")
        candidates = ([Path(configured).expanduser() / "sessions"] if configured else [])
        candidates += [Path.home() / ".dsh" / "sessions", Path.home() / "dsh-home" / "sessions"]
    else:
        candidates = [Path(dsh_dir).expanduser()]
    roots = list(dict.fromkeys(candidate.resolve() for candidate in candidates if candidate.is_dir()))
    if not roots:
        return [], 0
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    files = sorted({path for root in roots for path in
                    list(root.rglob("session*.v4.jsonl")) + list(root.rglob("session*.v4.jsonl.zstd"))})
    sessions = []
    root_ids = {}
    by_id = {}
    for path in files:
        rows = _dsh_rows(path)
        header = next((row for row in rows if row.get("type") == "session"), None)
        if not header or not header.get("id"):
            continue
        sid = "deepseek:" + str(header["id"])
        cwd = header.get("cwd")
        events = []
        call_refs = {}
        for row in rows:
            at = base.parse_time(row.get("time"))
            if not at or at < cutoff:
                continue
            data = row.get("data") if isinstance(row.get("data"), dict) else {}
            kind = row.get("type")
            def add(event_kind, **fields):
                events.append({"id": f"{sid}:E{len(events)+1:06d}", "at": base.iso(at),
                               "kind": event_kind, **fields})
            source = data.get("source") if isinstance(data.get("source"), dict) else {}
            if kind == "user/message" and source.get("kind") == "user":
                content = _dsh_text(data.get("content"))
                if content:
                    add("message", role="user", text=base.redact(content, 12000))
            elif kind == "assistant/message":
                content = _dsh_text((data.get("message") or {}).get("content"))
                if content:
                    add("message", role="assistant", text=base.redact(content, 12000))
            elif kind == "tool/call":
                name = str(data.get("name") or "tool")
                command = base.command_from_input(data.get("arguments"))
                call_id = str(data.get("callId") or "")
                add("tool_call", tool=name, command=base.redact(command, 1800),
                    paths=base.paths_from_input(data.get("arguments"), command),
                    call_id=call_id, test_like=bool(base.TEST_PATTERN.search(command)))
                if call_id:
                    call_refs[call_id] = events[-1]["id"]
            elif kind == "tool/result":
                message = data.get("message") or {}
                call_id = str(message.get("toolCallId") or "")
                content = _dsh_text(message.get("content"))
                add("tool_result", call_id=call_id, call_ref=call_refs.get(call_id),
                    text=base.redact(content, 1200), is_error=bool(message.get("isError")))
        if not events:
            continue
        events.sort(key=lambda event: (event["at"], event["id"]))
        is_root = path.name.startswith("session.v4.")
        session = {"id": sid, "source": "deepseek", "cwd": cwd,
                   "parent_id": None, "started_at": events[0]["at"],
                   "ended_at": events[-1]["at"], "event_count": len(events),
                   "source_file_count": 1, "empty_transcript": False, "events": events,
                   "_delegation_depth": header.get("delegationDepth", 0),
                   "_session_dir": str(path.parent)}
        if sid not in by_id or len(events) > by_id[sid]["event_count"]:
            by_id[sid] = session
        if is_root:
            root_ids[str(path.parent)] = sid
    sessions = list(by_id.values())
    for session in sessions:
        if session.pop("_delegation_depth", 0):
            session["parent_id"] = root_ids.get(session["_session_dir"])
        session.pop("_session_dir", None)
    return sessions, len(files)


OPENCODE_TOOL_MAP = {
    "bash": "Bash", "edit": "Edit", "write": "Write", "read": "Read",
    "grep": "Grep", "glob": "Glob", "webfetch": "WebFetch", "websearch": "WebSearch",
    "todowrite": "TodoWrite", "task": "Task", "skill": "Skill", "apply_patch": "ApplyPatch",
}


def _opencode_json(value):
    if isinstance(value, dict):
        return value
    try:
        parsed = json.loads(value or "{}")
    except (TypeError, ValueError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _opencode_command(tool, payload):
    if not isinstance(payload, dict):
        return str(payload or "")[:800]
    for key in ("command", "cmd"):
        if payload.get(key):
            return str(payload[key])[:800]
    if tool in ("edit", "write", "read", "grep", "glob"):
        target = payload.get("filePath") or payload.get("path") or payload.get("pattern")
        if target:
            return f"{tool} {target}"[:800]
    return json.dumps(payload, ensure_ascii=False)[:800]


def _opencode_paths(payload):
    if not isinstance(payload, dict):
        return []
    found = []
    for key in ("filePath", "path", "file_path", "files"):
        value = payload.get(key)
        if isinstance(value, str):
            found.append(value)
        elif isinstance(value, list):
            found.extend(str(item) for item in value if isinstance(item, str))
    return sorted({path for path in found if path})[:20]


def collect_opencode(days, opencode_dir):
    """Read opencode's SQLite sessions without exporting or mutating the DB."""
    root = Path(opencode_dir).expanduser()
    if root.is_file():
        dbs = [root]
    elif root.is_dir():
        dbs = sorted(path for path in root.glob("opencode*.db")
                     if path.is_file() and not path.name.endswith(("-wal", "-shm")))
    else:
        return [], 0
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    cutoff_ts = cutoff.timestamp()
    sessions, seen = [], set()
    for db in dbs:
        try:
            connection = sqlite3.connect("file:" + str(db.resolve()) + "?mode=ro", uri=True, timeout=5)
            connection.row_factory = sqlite3.Row
            rows = connection.execute(
                "SELECT id, parent_id, directory, title, time_created, time_updated FROM session"
            ).fetchall()
        except sqlite3.Error:
            continue
        for row in rows:
            sid = "opencode:" + sha256((str(db.resolve()) + ":" + str(row["id"])).encode()).hexdigest()[:16]
            if sid in seen:
                continue
            parent = row["parent_id"]
            parent_id = ("opencode:" + sha256((str(db.resolve()) + ":" + str(parent)).encode()).hexdigest()[:16]
                         if parent else None)
            try:
                messages = connection.execute(
                    "SELECT id, time_created, data FROM message WHERE session_id = ? ORDER BY time_created",
                    (row["id"],)).fetchall()
            except sqlite3.Error:
                continue
            events = []
            call_refs = {}
            for message in messages:
                meta = _opencode_json(message["data"])
                role = str(meta.get("role") or "assistant")
                if role not in ("user", "assistant"):
                    continue
                created = (meta.get("time") or {}).get("created") or message["time_created"]
                try:
                    at = base.iso(datetime.fromtimestamp(float(created) / 1000, timezone.utc))
                except (TypeError, ValueError, OSError):
                    continue
                if base.parse_time(at).timestamp() < cutoff_ts:
                    continue
                try:
                    parts = connection.execute(
                        "SELECT time_created, data FROM part WHERE message_id = ? ORDER BY time_created",
                        (message["id"],)).fetchall()
                except sqlite3.Error:
                    parts = []
                texts = []
                tool_events = []
                for part in parts:
                    payload = _opencode_json(part["data"])
                    kind = payload.get("type")
                    if kind == "text":
                        text = str(payload.get("text") or "").strip()
                        if text:
                            texts.append(text)
                    elif kind == "tool" and role == "assistant":
                        state = payload.get("state") if isinstance(payload.get("state"), dict) else {}
                        tool_raw = str(payload.get("tool") or "tool")
                        tool = OPENCODE_TOOL_MAP.get(tool_raw, tool_raw.title())
                        call_id = str(payload.get("callID") or "")
                        payload_input = state.get("input")
                        command = _opencode_command(tool_raw, payload_input)
                        tool_events.append({
                            "kind": "tool_call", "tool": tool,
                            "command": base.redact(command, 1800),
                            "paths": _opencode_paths(payload_input),
                            "call_id": call_id, "test_like": bool(base.TEST_PATTERN.search(command)),
                            "_result": str(state.get("output") or state.get("error") or ""),
                            "_is_error": bool(state.get("error")) or str(state.get("status") or "") in ("error", "failed"),
                        })
                if texts and role in ("user", "assistant"):
                    events.append({"id": f"{sid}:E{len(events)+1:06d}", "at": at,
                                   "kind": "message", "role": role,
                                   "text": base.redact("\n".join(texts), 12000)})
                for item in tool_events:
                    result_text = item.pop("_result")
                    is_error = item.pop("_is_error")
                    events.append({"id": f"{sid}:E{len(events)+1:06d}", "at": at, **item})
                    if item.get("call_id"):
                        call_refs[item["call_id"]] = events[-1]["id"]
                    if result_text:
                        events.append({"id": f"{sid}:E{len(events)+1:06d}", "at": at,
                                       "kind": "tool_result", "tool": item["tool"],
                                       "call_id": item.get("call_id", ""),
                                       "call_ref": call_refs.get(item.get("call_id", "")),
                                       "text": base.redact(result_text, 1200),
                                       "is_error": is_error})
            if not events:
                continue
            events.sort(key=lambda event: (event["at"], event["id"]))
            started, ended = events[0]["at"], events[-1]["at"]
            if base.parse_time(ended).timestamp() < cutoff_ts:
                continue
            seen.add(sid)
            sessions.append({"id": sid, "source": "opencode", "cwd": row["directory"] or "",
                             "parent_id": parent_id, "started_at": started, "ended_at": ended,
                             "event_count": len(events), "source_file_count": 1,
                             "empty_transcript": False, "events": events})
        connection.close()
    return sessions, len(dbs)


def collect_local_commits(sessions, days=None):
    """Attach repos and collect local commits for an all-local session set."""
    by_repo = {}
    for session in sessions:
        cwd = session.get("cwd")
        if not cwd:
            continue
        root = base.git("rev-parse", "--show-toplevel", cwd=cwd)
        if not root:
            continue
        repo = str(Path(root).resolve())
        session["repo"] = repo
        session["repo_name"] = Path(repo).name
        by_repo.setdefault(repo, []).append(session)
    commits, seen, repos = [], set(), []
    for repo, group in sorted(by_repo.items()):
        found = base.collect_repo_commits(repo, group)
        for commit in found:
            key = (commit.get("repo"), commit.get("sha"))
            if key not in seen:
                seen.add(key)
                commits.append(commit)
        repos.append({"path": repo, "name": Path(repo).name,
                      "sessions": len(group), "commits": len(found)})
    commits.sort(key=lambda commit: commit.get("at") or "")
    return commits, repos


def collect_all_local(days, codex_dir, claude_dir, hermes_db, dsh_dir=None, opencode_dir="~/.local/share/opencode"):
    claude = base.collect_all_claude(days, claude_dir)
    codex, codex_files = collect_codex(days, codex_dir)
    hermes, hermes_records = collect_hermes(days, hermes_db)
    deepseek, deepseek_files = collect_deepseek(days, dsh_dir)
    opencode, opencode_dbs = collect_opencode(days, opencode_dir)
    sessions = claude["sessions"] + codex + hermes + deepseek + opencode
    sessions.sort(key=lambda session: session["started_at"])
    stats, touched = _session_counts(sessions)
    coverage = {"files_scanned": claude["coverage"]["files_scanned"] + codex_files + hermes_records + deepseek_files + opencode_dbs,
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


def collect_project(project, days, codex_dir, claude_dir, hermes_db, dsh_dir=None,
                    opencode_dir="~/.local/share/opencode"):
    project = base.canonical_project(project)
    hermes, _ = collect_hermes(days, hermes_db)
    deepseek, _ = collect_deepseek(days, dsh_dir)
    opencode, _ = collect_opencode(days, opencode_dir)
    selected = [session for session in hermes + deepseek + opencode
                if base.project_matches(session["cwd"], project)]
    return base.collect(project, days, codex_dir, claude_dir, extra_sessions=selected)
