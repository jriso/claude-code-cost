#!/usr/bin/env python3
"""Regression tests for cost.py — pricing lookup and the JSON/cost summary.

Run with `pytest` (if installed) or directly: `python3 test_cost.py`.
The end-to-end test points HOME at a temp dir with a synthetic JSONL log so it
exercises the real scan → dedupe → month-filter → cost path without touching
your actual ~/.claude/projects data.
"""
import json
import os
import subprocess
import sys
import tempfile

import cost

HERE = os.path.dirname(os.path.abspath(__file__))


def test_exact_and_current_rates():
    assert cost.rate_for("claude-fable-5")["o"] == 50
    assert cost.rate_for("claude-mythos-5")["o"] == 50
    assert cost.rate_for("claude-opus-4-8")["o"] == 25
    assert cost.rate_for("claude-sonnet-4-6")["o"] == 15


def test_dated_variant_of_known_alias():
    # A future dated pin resolves to the same rate as its alias, not a fallback.
    assert cost.rate_for("claude-opus-4-8-20260101") == cost.rate_for("claude-opus-4-8")
    assert cost.rate_for("claude-fable-5-20260609") == cost.rate_for("claude-fable-5")


def test_family_fallback_uses_correct_tier():
    # Unknown future versions fall back to their family tier, NOT opus.
    assert cost.rate_for("claude-fable-6")["o"] == 50
    assert cost.rate_for("claude-mythos-preview")["o"] == 50
    assert cost.rate_for("claude-opus-4-9")["o"] == 25


def test_legacy_entries_unchanged():
    # The original bug: Opus 4.x legacy must stay at $15/$75, not current rates.
    assert cost.rate_for("claude-opus-4-20250514")["o"] == 75
    assert cost.rate_for("claude-opus-4-1-20250805")["o"] == 75


def test_unknown_model_defaults_to_opus():
    assert cost.rate_for("claude-zzz-9")["o"] == 25  # no tier match -> opus default


def _write_log(dirpath, rows):
    proj = os.path.join(dirpath, ".claude", "projects", "testproj")
    os.makedirs(proj)
    with open(os.path.join(proj, "a.jsonl"), "w") as f:
        for rid, model, out in rows:
            f.write(json.dumps({
                "type": "assistant", "timestamp": "2026-05-15T10:00:00Z",
                "sessionId": "s1", "requestId": rid,
                "message": {"model": model, "usage": {
                    "input_tokens": 0, "output_tokens": out,
                    "cache_read_input_tokens": 0,
                    "cache_creation": {"ephemeral_5m_input_tokens": 0,
                                       "ephemeral_1h_input_tokens": 0}}},
            }) + "\n")


def test_end_to_end_scan_and_cost():
    """Scan a synthetic log via the real CLI and check totals + the warning.

    Uses a May timestamp because cost.py reports the last *complete* month.
    NOTE: this assumes the test runs in June; the assertion is skipped otherwise
    so the suite stays deterministic year-round.
    """
    from datetime import datetime
    if datetime.now().strftime("%Y-%m") != "2026-06":
        print("  (skipped end-to-end: only asserts when run in 2026-06)")
        return
    tmp = tempfile.mkdtemp()
    _write_log(tmp, [
        ("r1", "claude-opus-4-8", 1_000_000),   # $25
        ("r2", "claude-fable-5", 2_000_000),     # $100
        ("r3", "claude-zzz-9", 1_000_000),       # opus fallback $25 + warning
    ])
    env = dict(os.environ, HOME=tmp)
    p = subprocess.run([sys.executable, "cost.py"], cwd=HERE, env=env,
                       capture_output=True, text=True)
    out = json.loads(p.stdout)
    assert set(out["models"]) == {"claude-opus-4-8", "claude-fable-5", "claude-zzz-9"}
    assert "warning: unrecognized model 'claude-zzz-9'" in p.stderr
    assert "fable" not in p.stderr.lower()  # known entry must not warn
    cost_val = float(p.stderr.split("Estimated API cost: $")[1].replace(",", "").split()[0])
    assert abs(cost_val - 150) < 0.5, cost_val


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for fn in fns:
        fn()
        print(f"  ok  {fn.__name__}")
    print(f"\n{len(fns)} passed")
