# -*- coding: utf-8 -*-
"""Deterministic trajectory layer modeled on the public Paxel client.

This module does not call a model. It turns the normalized session events into
typed events, per-session signals, steering traces, decision chains and
decision outcomes, then renders a local timeline.
"""

from datetime import datetime, timezone
import html
import json
import re
from statistics import median

MAX_EVENTS = 3000
STRUCTURAL_TYPES = {"subagent_dispatch", "subagent_return", "git_commit"}
MAX_TEXT = 10000
MAX_BASH = 5000
MAX_RESULT = 1200

PLAN_PATH_PATTERN = re.compile(r"(?:\.claude/plans/[^/]+\.md|(?:^|/)(?:[A-Z][A-Z0-9_]*_)?PLAN\.md)$")
TEST_COMMAND_PATTERN = re.compile(r"\b(?:pytest|py\.test|jest|vitest|mocha|rspec|cargo\s+test|go\s+test|npm\s+(?:run\s+)?test|pnpm\s+test|yarn\s+test|bun\s+test)\b", re.I)
ERROR_PATTERN = re.compile(r"(?:Traceback \(most recent call last\)|(?:^|\n)\s*(?:Error|Exception|FAILED|panic:)\b|exit code [1-9]|command not found|No such file or directory)", re.I)
GIT_COMMIT_PATTERN = re.compile(r"\bgit\s+commit\b")
GIT_BRANCH_PATTERN = re.compile(r"\bgit\s+(?:checkout|switch)\b")
GIT_PUSH_PATTERN = re.compile(r"\bgit\s+push\b")
GIT_WORKTREE_PATTERN = re.compile(r"\bgit\s+worktree\b")
SUBAGENT_TOOL_PATTERN = re.compile(r"^(?:Task|Agent|subagent|agent_task)$", re.I)
EDIT_TOOLS = {"Edit", "MultiEdit", "ApplyPatch", "apply_patch"}
CREATE_TOOLS = {"Write"}
READ_TOOLS = {"Read"}
TOOL_ALIASES = {"terminal": "Bash", "exec": "Bash", "shell": "Bash",
                "read_file": "Read", "write_file": "Write", "patch": "Edit",
                "apply_patch": "ApplyPatch", "search_files": "Grep", "todo": "TodoWrite",
                "web_search": "WebSearch", "web_extract": "WebFetch", "skill_view": "Skill"}

PROPOSAL_PATTERNS = (
    re.compile(r"(?:option|approach|alternative|choice)\s*(?:\d|[A-C])", re.I),
    re.compile(r"\d+[.)]\s+\*\*[^*]+\*\*"),
    re.compile(r"(?:we could|you could|options are|alternatives):", re.I),
    re.compile(r"(?:here are|there are)\s+(?:\d+|several|a few)\s+(?:options|approaches|ways)", re.I),
    re.compile(r"(?:trade-?off|pros?\s+and\s+cons?|versus|vs\.?)\b", re.I),
    re.compile(r"(?:would you (?:like|prefer|rather)|what (?:do you think|approach)|should (?:I|we)|how (?:do you want|should))", re.I),
    re.compile(r"(?:which (?:option|approach)|do you want me to)\b", re.I),
)

STEERING_ACTIONS = {
    "explore": re.compile(r"let's (try|explore|look at|investigate|check)|what if|how about|i wonder", re.I),
    "constrain": re.compile(r"\b(must|should|always|never|require|constraint|rule)\b.*\b(use|follow|apply|enforce)\b", re.I),
    "delegate": re.compile(r"\b(implement|build|create|write|add|fix|update|deploy|run|test|make)\b", re.I),
    "inspect": re.compile(r"\b(show me|let me see|what does|how does|look at|check|verify|review)\b", re.I),
    "reject": re.compile(r"\b(no|wrong|bad|don't|stop|that's not|revert|undo|scratch that|kill)\b", re.I),
    "redirect": re.compile(r"\b(instead|rather|alternatively|change|switch|pivot|reframe|actually)\b", re.I),
    "verify": re.compile(r"\b(test|verify|check|confirm|make sure|does it|is it|run the)\b", re.I),
    "ship": re.compile(r"\b(ship|deploy|push|release|merge|commit|done|good to go|looks good)\b", re.I),
    "debug": re.compile(r"\b(why|error|fail|bug|broken|wrong|issue|crash|exception|trace)\b", re.I),
    "recover": re.compile(r"\b(fix|resolve|recover|restore|rollback|patch|workaround|fallback)\b", re.I),
}

CRITIQUE_PATTERN = re.compile(r"rate me|evaluate|how am i doing|critique|review my|what do you think of my", re.I)
SELF_CORRECTION_PATTERN = re.compile(r"\bactually\b|wait,|no,\s|let me rethink|scratch that|on second thought", re.I)
KILL_PATTERN = re.compile(r"\bdelete\b|\bremove\b|\bdrop\b|\bkill\b|get rid of|rip out|revert", re.I)
HYPOTHESIS_PATTERN = re.compile(r"i think.*because|my theory is|i suspect|probably.*caused by|the issue is likely", re.I)
DOMAIN_CORRECTION_PATTERN = re.compile(r"that's not how|actually it should|no,.*works like|you're wrong about", re.I)
PRODUCT_PATTERN = re.compile(r"\bcustomer\b|\bUX\b|user experience|onboarding|\bfriction\b|pain point|product decision|user need|user research", re.I)
NARRATIVE_PATTERN = re.compile(r"story|narrative|context|framework|profile|comprehensive|big picture", re.I)
REVIEW_PATTERN = re.compile(r"looks wrong|check if|verify|doesn't look right|are you sure|let me see", re.I)
CONFIRMATION_PATTERN = re.compile(r"should i proceed|shall i|do you want me to|is that ok", re.I)
ARCHITECTURE_PATTERN = re.compile(r"\barchitect(?:ure|ural)\b|\babstraction\b|\bdecoupl|\bcoupling\b|separation of concerns|design pattern|\bmodular|refactor into", re.I)
DEBUGGING_PATTERN = re.compile(r"why|broken|error|fail|bug|wrong|issue|crash|exception|stack trace", re.I)
GRATITUDE_PATTERN = re.compile(r"\bthank you\b|\bthanks\b(?! to\b)|\bthx\b|\bappreciate (?:it|that|this|you|the)\b", re.I)
IMPERATIVE_VERBS = {"add", "build", "create", "implement", "write", "fix", "update", "refactor", "deploy", "run", "test", "make", "delete", "remove"}


def _parse_time(value):
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def _iso(value):
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z") if value else None


def _truncate(value, limit):
    text = str(value or "")
    return text[:limit]


def _clean(value):
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _words(text):
    return len(re.findall(r"[A-Za-z0-9_]+|[\u4e00-\u9fff]", text or ""))


def _test_result(text, tool, command):
    text = (text or "")[-30000:]
    if not text:
        return None
    if not (TEST_COMMAND_PATTERN.search(command or "") or "test" in (tool or "").lower()
            or re.search(r"\b(?:passed|failed|passing|failing)\b", text, re.I)):
        return None
    patterns = (
        ("pytest", re.compile(r"(\d+)\s+passed(?:,\s*(\d+)\s+failed)?")),
        ("jest", re.compile(r"Tests:\s+(?:(\d+)\s+failed,\s*)?(\d+)\s+passed")),
        ("cargo", re.compile(r"(\d+)\s+passed;\s*(\d+)\s+failed")),
        ("rspec", re.compile(r"(\d+)\s+examples?,\s*(\d+)\s+failures?")),
        ("generic", re.compile(r"(\d+)\s+passing(?:,\s*(\d+)\s+failing)?")),
    )
    for framework, pattern in patterns:
        match = pattern.search(text)
        if not match:
            continue
        groups = [g for g in match.groups()]
        if framework == "jest":
            failed = int(groups[0] or 0) if len(groups) > 0 else 0
            passed = int(groups[1] or 0) if len(groups) > 1 else 0
        elif framework == "cargo":
            passed, failed = int(groups[0] or 0), int(groups[1] or 0)
        else:
            passed, failed = int(groups[0] or 0), int(groups[1] or 0) if len(groups) > 1 else 0
        return {"framework": framework, "passed": passed, "failed": failed, "pending": 0}
    return None


def _git_details(command):
    message = ""
    match = re.search(r"-m\s+(['\"])(.*?)\1", command or "", re.S)
    if match:
        message = _clean(match.group(2))
    branch = ""
    match = re.search(r"git\s+(?:checkout|switch)\s+(?:-b\s+|-c\s+)?([^\s;&|]+)", command or "")
    if match:
        branch = match.group(1)
    return message, branch


def _proposal_type(text):
    lower = (text or "").lower()
    if "trade-off" in lower or "tradeoff" in lower or "pros and cons" in lower:
        return "tradeoff"
    if re.search(r"would you (?:like|prefer|rather)|which (?:option|approach)|do you want me to", text or "", re.I):
        return "question"
    if re.search(r"\d+[.)]\s+", text or ""):
        return "options"
    return "question"


def _option_count(text):
    return len(re.findall(r"(?:^|\n)\s*(?:\d+[.)]|[-*])\s+", text or ""))


def typed_events(session):
    """Turn normalized message/tool events into Paxel-style typed events."""
    events = []
    calls = {}
    dispatches = set()

    def add(kind, source, **fields):
        item = {"id": f"{session['id']}:T{len(events)+1:05d}", "type": kind,
                "at": source.get("at"), "source_event_id": source.get("id")}
        item.update(fields)
        events.append(item)
        return item

    for event in session.get("events", []):
        kind = event.get("kind")
        if kind == "message":
            role = event.get("role")
            text = event.get("text", "")
            if role == "user":
                add("user_directive", event, text=_truncate(text, MAX_TEXT))
            elif role == "assistant" and any(pattern.search(text) for pattern in PROPOSAL_PATTERNS):
                add("agent_proposal", event, text=_truncate(text, MAX_TEXT),
                    proposal_type=_proposal_type(text), option_count=_option_count(text))
            continue

        if kind == "tool_call":
            raw_tool = str(event.get("tool") or "")
            tool = TOOL_ALIASES.get(raw_tool, raw_tool)
            command = str(event.get("command") or "")
            if raw_tool in ("exec", "shell"):
                nested = re.search(r"\b(?:cmd|command)\s*:\s*(['\"])(.*?)\1", command, re.S)
                if nested:
                    command = nested.group(2)
            paths = [str(path) for path in (event.get("paths") or []) if path]
            call_id = str(event.get("call_id") or "")
            if tool in EDIT_TOOLS:
                for path in paths:
                    add("file_edit", event, path=path)
            elif tool in CREATE_TOOLS:
                for path in paths:
                    add("file_create", event, path=path)
            elif tool in READ_TOOLS:
                for path in paths:
                    add("file_read", event, path=path)
            elif tool.lower() in {"bash", "exec_command", "shell_command"}:
                if GIT_COMMIT_PATTERN.search(command):
                    message, _ = _git_details(command)
                    add("git_commit", event, message=message)
                elif GIT_BRANCH_PATTERN.search(command):
                    _, branch = _git_details(command)
                    add("git_branch_switch", event, branch=branch)
                elif GIT_PUSH_PATTERN.search(command):
                    add("git_push", event)
                else:
                    add("bash_command", event, command=_truncate(command, MAX_BASH))
            elif SUBAGENT_TOOL_PATTERN.match(tool):
                dispatch = add("subagent_dispatch", event, tool_use_id=call_id,
                               subagent_id=tool, description=_truncate(command, 200),
                               prompt_hash="", run_in_background=False)
                dispatches.add(call_id)
            else:
                add("tool_call", event, tool=tool, command=_truncate(command, 2000))
            if call_id:
                calls[call_id] = events[-1] if events else None
            continue

        if kind == "tool_result":
            call_ref = str(event.get("call_ref") or "")
            call = calls.get(call_ref)
            result_text = str(event.get("text") or "")
            if call is not None:
                call["result_status"] = "error" if event.get("is_error") else "ok"
                call["result_excerpt"] = _truncate(result_text, MAX_RESULT)
            if event.get("is_error") or ERROR_PATTERN.search(result_text):
                add("error_encountered", event, message=_truncate(result_text, MAX_TEXT))
            test = _test_result(result_text, event.get("tool"), event.get("command"))
            if test:
                add("test_run", event, **test)
            if call_ref and call_ref in dispatches:
                add("subagent_return", event, tool_use_id=call_ref,
                    return_text_length=len(result_text),
                    indicates_error=bool(event.get("is_error")))
    return events


def extract_signals(session, events):
    raw = session.get("events", [])
    messages = [event for event in raw if event.get("kind") == "message"]
    users = [event for event in messages if event.get("role") == "user"]
    assistants = [event for event in messages if event.get("role") == "assistant"]
    tool_calls = [event for event in raw if event.get("kind") == "tool_call"]
    texts = [event.get("text", "") for event in users]
    user_words = sum(_words(text) for text in texts)

    times = sorted({_parse_time(event.get("at")) for event in raw if _parse_time(event.get("at"))})
    active_seconds = 0.0
    for previous, current in zip(times, times[1:]):
        delta = (current - previous).total_seconds()
        if 0 <= delta <= 90 * 60:
            active_seconds += delta
    duration = active_seconds / 60.0

    file_paths = sorted({item.get("path") for item in events if item["type"] in ("file_edit", "file_create") and item.get("path")})
    tests = [item for item in events if item["type"] == "test_run"]
    passed = sum(int(item.get("passed", 0)) for item in tests)
    failed = sum(int(item.get("failed", 0)) for item in tests)
    errors = [item for item in events if item["type"] == "error_encountered"]
    commits = [item for item in events if item["type"] == "git_commit"]
    dispatches = [item for item in events if item["type"] == "subagent_dispatch"]
    returns = [item for item in events if item["type"] == "subagent_return"]
    plan_creates = [item for item in events if item["type"] in ("file_create", "file_edit")
                    and PLAN_PATH_PATTERN.search(item.get("path", ""))]

    first_test = next((index for index, item in enumerate(events) if item["type"] == "test_run"), None)
    first_edit = next((index for index, item in enumerate(events)
                       if item["type"] in ("file_edit", "file_create") and not PLAN_PATH_PATTERN.search(item.get("path", ""))), None)
    tdd = None
    if first_test is not None or first_edit is not None:
        tdd = 1.0 if first_test is not None and (first_edit is None or first_test < first_edit) else 0.0

    recoveries = []
    for index, item in enumerate(events):
        if item["type"] != "error_encountered":
            continue
        for distance, following in enumerate(events[index + 1:], 1):
            if following["type"] == "test_run" and int(following.get("passed", 0)) > 0:
                recoveries.append(distance)
                break
            if following["type"] == "git_commit":
                recoveries.append(distance)
                break
    recovery = round(float(median(recoveries)), 1) if recoveries else None

    retries = 0
    for index, item in enumerate(events):
        if item["type"] != "error_encountered":
            continue
        command = None
        for previous in reversed(events[:index]):
            if previous["type"] == "bash_command":
                command = previous.get("command")
                break
        if not command:
            continue
        for following in events[index + 1:index + 6]:
            if following["type"] == "bash_command" and following.get("command") == command:
                retries += 1
                break

    def count(pattern):
        return sum(1 for text in texts if pattern.search(text))

    imperative = sum(1 for text in texts if (text.strip().split() or [""])[0].lower() in IMPERATIVE_VERBS)
    short = {}
    for text in texts:
        normalized = _clean(text).lower()
        if 1 <= _words(normalized) <= 8:
            short.setdefault(normalized, []).append(_clean(text))
    repeated = [{"norm": key, "count": len(values), "example": values[0][:200]}
                for key, values in short.items() if len(values) > 1][:40]

    signals = {
        "message_count": len(messages),
        "user_message_count": len(users),
        "assistant_count": len(assistants),
        "total_user_words": user_words,
        "avg_prompt_length_words": round(user_words / len(users), 2) if users else 0,
        "tool_count": len(tool_calls),
        "unique_tools": len({event.get("tool") for event in tool_calls if event.get("tool")}),
        "tools_per_message": round(len(tool_calls) / len(messages), 2) if messages else 0,
        "duration_minutes": round(duration, 1),
        "messages_per_minute": round(len(messages) / duration, 2) if duration else 0,
        "git_commit_count": len(commits),
        "files_modified_count": len(file_paths),
        "test_run_count": len(tests),
        "test_pass_rate": round(passed / (passed + failed), 3) if passed + failed else None,
        "error_count": len(errors),
        "plan_mode_used": bool(plan_creates),
        "plan_file_count": len(plan_creates),
        "task_tool_used": bool(dispatches),
        "subagent_dispatch_count": len(dispatches),
        "subagent_return_count": len(returns),
        "worktree_used": any(GIT_WORKTREE_PATTERN.search(item.get("command", "")) for item in events if item["type"] == "bash_command"),
        "imperative_prompts": imperative,
        "confirmation_requests": count(CONFIRMATION_PATTERN),
        "kill_decisions": count(KILL_PATTERN),
        "review_checks": count(REVIEW_PATTERN),
        "self_corrections": count(SELF_CORRECTION_PATTERN),
        "critiques": count(CRITIQUE_PATTERN),
        "domain_corrections": count(DOMAIN_CORRECTION_PATTERN),
        "hypothesis_driven": count(HYPOTHESIS_PATTERN),
        "debugging_messages": count(DEBUGGING_PATTERN),
        "architecture_discussions": count(ARCHITECTURE_PATTERN),
        "narrative_framing": count(NARRATIVE_PATTERN),
        "product_references": count(PRODUCT_PATTERN),
        "substantive_messages": sum(1 for text in texts if _words(text) >= 5),
        "terse_messages": sum(1 for text in texts if _words(text) <= 2),
        "courtesy_messages": count(GRATITUDE_PATTERN),
        "prompt_types": {"directive": imperative, "other": max(0, len(users) - imperative)},
        "repeated_prompts": repeated,
        "event_type_counts": {kind: sum(1 for item in events if item["type"] == kind)
                              for kind in sorted({item["type"] for item in events})},
    }
    if tdd is not None:
        signals["tdd_discipline_ratio"] = tdd
    if recovery is not None:
        signals["recovery_speed"] = recovery
    if errors:
        signals["error_retry_ratio"] = round(retries / len(errors), 3)
    if len(events) > MAX_EVENTS:
        signals["session_events_truncated"] = True
    if users:
        signals["substantive_ratio"] = round(signals["substantive_messages"] / len(users), 2)
    return signals


def extract_steering(events):
    actions = []
    counts = {}
    for line, event in enumerate(events):
        if event["type"] != "user_directive":
            continue
        text = _clean(event.get("text"))
        if not text:
            continue
        for action, pattern in STEERING_ACTIONS.items():
            if pattern.search(text):
                actions.append({"type": action, "line": line, "text": text[:200],
                                "event_id": event.get("id")})
                counts[action] = counts.get(action, 0) + 1
    return {"actions": actions[:100], "action_counts": counts, "total_actions": len(actions)}


def _decision_layer(decisions, events):
    index = {event.get("source_event_id"): position for position, event in enumerate(events)}
    outcomes = []
    enriched = []
    for decision in decisions or []:
        item = dict(decision)
        response_index = index.get(item.get("response_ref"))
        item["event_index"] = response_index
        enriched.append(item)
        if response_index is None:
            continue
        outcome = None
        weak = None
        for distance, event in enumerate(events[response_index + 1:response_index + 31], 1):
            if event["type"] not in ("git_commit", "test_run", "error_encountered", "file_edit", "file_create"):
                continue
            if event["type"] == "git_commit":
                signal = "positive"
            elif event["type"] == "test_run":
                signal = "positive" if int(event.get("failed", 0)) == 0 and int(event.get("passed", 0)) > 0 else "negative"
            elif event["type"] == "error_encountered":
                signal = "negative"
            else:
                signal = "neutral"
            candidate = {"decision_id": item.get("id"), "outcome_type": event["type"],
                         "outcome_event_id": event.get("id"), "distance": distance,
                         "outcome_signal": signal,
                         "temporal_layer": "immediate" if distance <= 5 else "short" if distance <= 15 else "medium" if distance <= 30 else "long",
                         "confidence": 0.6 if event["type"] in ("git_commit", "test_run", "error_encountered") else 0.4}
            if event["type"] in ("git_commit", "test_run", "error_encountered"):
                outcome = candidate
                break
            if weak is None:
                weak = candidate
        outcome = outcome or weak
        if outcome:
            outcomes.append(outcome)
    return enriched, outcomes


def _decision_chains(session_id, decisions):
    ordered = [item for item in decisions if item.get("event_index") is not None]
    ordered.sort(key=lambda item: item["event_index"])
    chains, current = [], []
    for item in ordered:
        if current and item["event_index"] - current[-1]["event_index"] > 10:
            chains.append(current)
            current = []
        current.append(item)
    if current:
        chains.append(current)
    edges = []
    chain_rows = []
    for number, chain in enumerate(chains, 1):
        if len(chain) < 2:
            continue
        chain_id = f"{session_id}:CHAIN{number}"
        for position, item in enumerate(chain, 1):
            item["exchange_chain_id"] = chain_id
            item["chain_position"] = position
            chain_rows.append({"chain_id": chain_id, "position": position, "decision_id": item.get("id")})
        for source, target in zip(chain, chain[1:]):
            edges.append({"source_decision_id": source.get("id"), "target_decision_id": target.get("id"),
                          "edge_type": "compounds", "discovered_by": "in_session", "confidence": 0.8})
    return edges, chain_rows


def build(sessions, decisions=None):
    decisions = decisions or {}
    rows = []
    for session in sessions:
        events = typed_events(session)
        signals = extract_signals(session, events)
        steering = extract_steering(events)
        session_decisions, outcomes = _decision_layer(decisions.get(session["id"], []), events)
        edges, chains = _decision_chains(session["id"], session_decisions)
        shown = []
        for event in events:
            if len(shown) >= MAX_EVENTS and event["type"] not in STRUCTURAL_TYPES:
                continue
            shown.append(event)
        rows.append({"session_id": session["id"], "source": session.get("source"),
                     "cwd": session.get("cwd"), "started_at": session.get("started_at"),
                     "ended_at": session.get("ended_at"), "parent_id": session.get("parent_id"),
                     "event_count": len(events), "events": shown, "signals": signals,
                     "steering": steering, "decisions": session_decisions,
                     "decision_edges": edges, "decision_chains": chains, "outcomes": outcomes})
    type_counts = {}
    for row in rows:
        for kind, value in row["signals"].get("event_type_counts", {}).items():
            type_counts[kind] = type_counts.get(kind, 0) + value
    return {"schema": "paxel-trajectory.v1",
            "generated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "sessions": rows,
            "summary": {"sessions": len(rows),
                        "typed_events": sum(row["event_count"] for row in rows),
                        "event_types": type_counts,
                        "decisions": sum(len(row["decisions"]) for row in rows),
                        "decision_edges": sum(len(row["decision_edges"]) for row in rows),
                        "outcomes": sum(len(row["outcomes"]) for row in rows),
                        "steering_actions": sum(row["steering"]["total_actions"] for row in rows)}}


def render_html(doc):
    escape = html.escape
    rows = sorted(doc.get("sessions", []), key=lambda row: row.get("event_count", 0), reverse=True)[:40]
    cards = []
    for row in rows:
        signals = row.get("signals", {})
        event_rows = []
        for event in row.get("events", [])[:100]:
            detail = event.get("text") or event.get("command") or event.get("path") or event.get("message") or ""
            extra = ""
            if event.get("result_status"):
                extra += f' <span class="status {escape(event["result_status"])}">{escape(event["result_status"])}</span>'
            if event.get("passed") is not None:
                extra += f' <span class="muted">passed={event.get("passed")} failed={event.get("failed")}</span>'
            event_rows.append(f'<div class="event"><span class="kind {escape(event["type"])}">{escape(event["type"])}</span>'
                              f'<time>{escape(str(event.get("at") or ""))}</time>'
                              f'<span class="detail">{escape(str(detail)[:500])}</span>{extra}</div>')
        signal_cells = "".join(
            f'<div class="metric"><span>{escape(key)}</span><b>{escape(str(value))}</b></div>'
            for key, value in signals.items()
            if key not in ("event_type_counts", "repeated_prompts", "prompt_types"))
        steering = "".join(f'<span class="chip">{escape(key)} {value}</span>'
                           for key, value in (row.get("steering", {}).get("action_counts") or {}).items())
        decisions = "".join(
            f'<li><b>{escape(str(item.get("decision_type") or item.get("classification") or "candidate"))}</b> '
            f'{escape(str(item.get("decision_narrative") or item.get("response_text") or "")[:220])}</li>'
            for item in row.get("decisions", [])[:8])
        outcomes = "".join(
            f'<li>{escape(str(item.get("outcome_type")))} · {escape(str(item.get("outcome_signal")))} · distance {item.get("distance")}</li>'
            for item in row.get("outcomes", [])[:8])
        cards.append(f'''<section class="session"><details><summary><b>{escape(str(row.get("source")))}</b> · {escape(row.get("session_id", ""))}
<span class="muted"> · {row.get("event_count", 0)} events · {escape(str(row.get("cwd") or ""))}</span></summary>
<div class="metrics">{signal_cells}</div>
<p>{steering}</p>
<h4>Timeline</h4><div class="timeline">{''.join(event_rows)}</div>
<h4>Decisions</h4><ul>{decisions or '<li class="muted">no candidates</li>'}</ul>
<h4>Outcomes</h4><ul>{outcomes or '<li class="muted">no linked outcome</li>'}</ul>
</details></section>''')
    summary = doc.get("summary", {})
    return f'''<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Paxel trajectory · 事件轨迹</title><style>
body{{margin:0;background:#fbfaf7;color:#24221f;font:14px/1.6 -apple-system,BlinkMacSystemFont,sans-serif}}
main{{max-width:1080px;margin:auto;padding:32px 20px 80px}}header{{border-top:3px solid #f07832;padding:20px 0}}
h1{{font-size:30px;margin:8px 0}}h4{{margin:16px 0 6px}}.muted{{color:#756f67}}.metrics{{display:grid;grid-template-columns:repeat(auto-fit,minmax(145px,1fr));gap:8px}}
.metric{{background:#fff;border:1px solid #e5dfd7;border-radius:8px;padding:8px}}.metric span{{display:block;color:#8c4b2d;font-size:11px}}
.metric b{{font-size:15px}}.session{{border-top:1px solid #ded7cb;padding:12px 0}}summary{{cursor:pointer;padding:6px 0}}
.timeline{{border-left:2px solid #e5dfd7;padding-left:10px}}.event{{display:grid;grid-template-columns:130px 72px 1fr;gap:8px;padding:3px 0;border-bottom:1px dotted #eee}}
.kind{{font-size:11px;font-weight:700;color:#8c4b2d}}.kind.error_encountered{{color:#b3261e}}.kind.git_commit{{color:#1b6b3a}}.kind.test_run{{color:#2b5f9e}}
time{{font-size:10px;color:#9a938a;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}}.detail{{overflow-wrap:anywhere}}.status{{font-size:10px;padding:1px 4px;border-radius:3px;background:#eee}}
.status.error{{background:#fde7e5;color:#b3261e}}.chip{{display:inline-block;background:#fff0e6;border-radius:10px;padding:2px 7px;margin:2px;font-size:11px}}
</style></head><body><main><header><div class="muted">LOCAL TRAJECTORY · 非 Paxel 官方报告</div><h1>事件轨迹与信号</h1>
<p class="muted">{summary.get("sessions", 0)} sessions · {summary.get("typed_events", 0)} typed events · {summary.get("decisions", 0)} decisions · {summary.get("decision_edges", 0)} edges · {summary.get("outcomes", 0)} outcomes</p>
<p class="muted">HTML 展示事件数最多的 {len(rows)} 个会话；完整数据在 trajectory.json。</p></header>
{''.join(cards)}</main></body></html>'''
