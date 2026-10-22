#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Offline smoke test for the deterministic trajectory layer."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "python"))
import trajectory as t


def main():
    session = {
        "id": "s1", "source": "test", "cwd": "/tmp/proj",
        "started_at": "2026-10-22T00:00:00Z", "ended_at": "2026-10-22T00:10:00Z",
        "events": [
            {"id": "e1", "at": "2026-10-22T00:00:00Z", "kind": "message", "role": "user",
             "text": "implement the parser and run tests"},
            {"id": "e2", "at": "2026-10-22T00:01:00Z", "kind": "message", "role": "assistant",
             "text": "Option 1: use regex\nOption 2: use parser"},
            {"id": "e3", "at": "2026-10-22T00:02:00Z", "kind": "message", "role": "user",
             "text": "Option 1, but actually use a parser"},
            {"id": "e4", "at": "2026-10-22T00:03:00Z", "kind": "tool_call", "tool": "Edit",
             "command": "edit parser.py", "paths": ["parser.py"], "call_id": "c1"},
            {"id": "e5", "at": "2026-10-22T00:04:00Z", "kind": "tool_result", "tool": "Edit",
             "call_ref": "c1", "text": "ok", "is_error": False},
            {"id": "e6", "at": "2026-10-22T00:05:00Z", "kind": "tool_call", "tool": "Bash",
             "command": "pytest -q", "paths": [], "call_id": "c2", "test_like": True},
            {"id": "e7", "at": "2026-10-22T00:06:00Z", "kind": "tool_result", "tool": "Bash",
             "call_ref": "c2", "text": "5 passed in 0.2s", "is_error": False},
            {"id": "e8", "at": "2026-10-22T00:07:00Z", "kind": "tool_call", "tool": "Task",
             "command": "review the parser", "paths": [], "call_id": "c3"},
            {"id": "e9", "at": "2026-10-22T00:08:00Z", "kind": "tool_result", "tool": "Task",
             "call_ref": "c3", "text": "found issue", "is_error": True},
            {"id": "e10", "at": "2026-10-22T00:09:00Z", "kind": "tool_call", "tool": "Bash",
             "command": "git commit -m 'add parser'", "paths": [], "call_id": "c4"},
            {"id": "e11", "at": "2026-10-22T00:10:00Z", "kind": "tool_result", "tool": "Bash",
             "call_ref": "c4", "text": "[main abc123] add parser", "is_error": False},
        ],
    }
    decisions = {"s1": [{"id": "s1:D001", "session_id": "s1", "source": "proposal_response",
                         "proposal_ref": "e2", "response_ref": "e3",
                         "proposal_text": "...", "response_text": "Option 1...",
                         "classification": "candidate"}]}
    doc = t.build([session], decisions)
    row = doc["sessions"][0]
    kinds = {event["type"] for event in row["events"]}
    assert {"user_directive", "agent_proposal", "file_edit", "bash_command", "test_run",
            "error_encountered", "subagent_dispatch", "subagent_return", "git_commit"} <= kinds, kinds
    assert row["signals"]["test_pass_rate"] == 1.0
    assert row["signals"]["git_commit_count"] == 1
    assert row["signals"]["subagent_dispatch_count"] == 1
    assert row["signals"]["tdd_discipline_ratio"] == 0.0
    assert row["outcomes"] and row["outcomes"][0]["outcome_signal"] == "positive"
    assert doc["summary"]["decision_edges"] == 0
    html = t.render_html(doc)
    assert "事件轨迹与信号" in html and "test_run" in html
    print("OK: trajectory typed events, signals, outcomes, and HTML")


if __name__ == "__main__":
    main()
