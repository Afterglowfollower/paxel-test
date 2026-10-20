#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Offline smoke test for the v15-v5 prompt profile.

No network and no model calls: fake callers return the shapes the official
prompts ask for, so the wiring (message construction, JSON parsing, axis
handling) is checked before spending GLM quota.

    python3 tools/smoke-prompt-profiles.py
"""
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "python"))

import paxel_reproduction as pipeline  # noqa: E402
import prompt_profiles as profiles  # noqa: E402


def main() -> int:
    try:
        prompts = profiles.load("v15-v5")
    except FileNotFoundError as exc:
        print(f"SKIP: {exc}")
        return 2

    assert prompts and set(prompts) == {"scoring", "narrative", "merge", "decision"}

    session = {
        "id": "smoke-session",
        "source": "claude",
        "cwd": "/tmp/smoke",
        "events": [
            {"id": "e1", "at": "2026-01-01T00:00:00", "kind": "message", "role": "user",
             "text": "please check the migration"},
            {"id": "e2", "at": "2026-01-01T00:00:01", "kind": "message", "role": "assistant",
             "text": "Option 1 or 2?"},
            {"id": "e3", "at": "2026-01-01T00:00:02", "kind": "message", "role": "user",
             "text": "Use option 2"},
        ],
    }
    metadata = profiles.session_metadata(session)
    message = profiles.narrative_user_message(metadata, "USER: hi\nASSISTANT: hi")
    assert "untrusted captured data" in message and "First prompt: please check" in message

    def fake_narrative(system, user, key, model, endpoint):
        assert "untrusted captured data" in user
        return (
            "## Goal\nG\n\n## What the Developer Decided\nD\n\n## Key Decisions\nK\n\n"
            "## Problems Encountered\nP\n\n## Observations\nO\n"
            "<session_intent>shipping</session_intent>"
        )

    narrative = pipeline.narrate_session(
        session, "key", "model", "endpoint", caller=fake_narrative,
        profile="v15-v5", prompts=prompts,
    )
    assert narrative["status"] == "generated" and narrative["intent"] == "shipping"

    candidates = [
        {"id": "s:D001", "session_id": "s", "source": "proposal_response", "proposal_ref": "a",
         "response_ref": "b", "proposal_text": "Option 1 or 2?", "response_text": "Use option 2",
         "classification": "candidate"},
        {"id": "s:D002", "session_id": "s", "source": "proactive_directive", "proposal_ref": None,
         "response_ref": "c", "proposal_text": "", "response_text": "verify the migration",
         "classification": "candidate"},
    ]

    def fake_decision(system, user, key, model, endpoint):
        assert "Classify these 2 exchanges" in user
        return (
            '[{"index":0,"is_decision":true,"decision_type":"option_selection",'
            '"confidence":"high","narrative":"picked 2","law_key":null},'
            '{"index":1,"is_decision":false}]'
        )

    classified = pipeline.extra.classify_decisions(
        candidates, "key", "model", "endpoint", fake_decision,
        profile="v15-v5", prompts=prompts,
    )
    assert classified[0]["classification"] == "confirmed"
    assert classified[0]["decision_type"] == "option_selection"
    assert classified[1]["classification"] == "rejected"

    data = {
        "episode_id": "episode:smoke",
        "type": "session_only",
        "link_confidence": 0.3,
        "commits": [],
        "insertions": 0,
        "deletions": 0,
        "sessions": [{
            "id": "smoke-session",
            "source": "claude",
            "first_prompt": {"text": "please check the migration", "ref": "e1"},
            "narrative": narrative["narrative"],
            "intent": narrative["intent"],
            "steering_refs": [],
            "test_observations": [],
            "decision_exchanges": classified,
            "code_review": None,
        }],
    }

    def fake_score(system, user, key, model, endpoint):
        assert user.startswith("Episode type: session_only")
        assert "## Session Narratives" in user
        return (
            '{"title":"t","facts":"f","interpretation":"i","counterweight":"c",'
            '"confidence":0.5,"scores":{"steering":6,"planning":5}}'
        )

    scored = pipeline.score_episode(
        data, "key", "model", "endpoint", caller=fake_score,
        profile="v15-v5", prompts=prompts,
    )
    assert scored["status"] == "scored"
    assert scored["scores"]["steering"] == 6.0
    assert scored["scores"]["planning"] == 5.0
    assert scored["scores"]["execution_leverage"] is None

    print("OK: v15-v5 prompt profile wiring")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
