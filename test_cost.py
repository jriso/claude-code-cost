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
    assert cost.rate_for("claude-fable-5-1")["cr"] == 0.25
    assert cost.rate_for("claude-fable-5")["cr"] == 1.00
    assert cost.rate_for("claude-opus-5-5")["o"] == 20
    assert cost.rate_for("claude-opus-5")["o"] == 25
    assert cost.rate_for("claude-sonnet-5-5")["cr"] == 0.10
    assert cost.rate_for("claude-sonnet-5")["cr"] == 0.20
    assert cost.rate_for("claude-opus-4-8")["o"] == 25
    assert cost.rate_for("claude-sonnet-4-6")["o"] == 15


def test_dated_variant_of_known_alias():
    # A dated pin resolves to the same rate as its alias, not a fallback.
    assert cost.rate_for("claude-opus-4-8-20260101") == cost.rate_for("claude-opus-4-8")
    assert cost.rate_for("claude-opus-5-5-20261201") == cost.rate_for("claude-opus-5-5")
    assert cost.rate_for("claude-fable-5-1-20261001") == cost.rate_for("claude-fable-5-1")


def test_point_release_not_matched_to_older_alias():
    # claude-opus-5-6 must not resolve to claude-opus-5.
    assert cost.rate_for("claude-opus-5-6") == cost.DEFAULT_RATES["opus"]
    assert cost.rate_for("claude-sonnet-5-6") == cost.DEFAULT_RATES["sonnet"]


def test_family_fallback_uses_correct_tier():
    # Unknown future versions fall back to their family tier, NOT opus.
    assert cost.rate_for("claude-fable-6")["o"] == 50
    assert cost.rate_for("claude-mythos-preview")["o"] == 50
    assert cost.rate_for("claude-opus-6") == cost.PRICING["claude-opus-5-5"]


def test_legacy_entries_unchanged():
    assert cost.rate_for("claude-opus-4-20250514")["o"] == 75
    assert cost.rate_for("claude-opus-4-1-20250805")["o"] == 75


def test_unknown_model_defaults_to_opus():
    assert cost.rate_for("claude-zzz-9") == cost.DEFAULT_RATES["opus"]


def test_long_prompt_tier():
    assert cost.bucket("claude-haiku-5-5", 100_000) == "claude-haiku-5-5"
    assert cost.bucket("claude-haiku-5-5", 100_001) == "claude-haiku-5-5:long"
    assert cost.bucket("claude-opus-5-5", 500_000) == "claude-opus-5-5"
    assert cost.rate_for("claude-haiku-5-5:long")["i"] == 0.50
    assert cost.rate_for("claude-haiku-5-5")["i"] == 0.10


def _last_month_ts():
    from datetime import datetime, timedelta
    d = datetime.now().replace(day=1) - timedelta(days=1)
    return d.replace(day=15).strftime("%Y-%m-%dT10:00:00Z")


def _write_log(dirpath, rows):
    proj = os.path.join(dirpath, ".claude", "projects", "testproj")
    os.makedirs(proj)
    ts = _last_month_ts()
    with open(os.path.join(proj, "a.jsonl"), "w") as f:
        for rid, model, out, prompt in rows:
            f.write(json.dumps({
                "type": "assistant", "timestamp": ts,
                "sessionId": "s1", "requestId": rid,
                "message": {"model": model, "usage": {
                    "input_tokens": prompt, "output_tokens": out,
                    "cache_read_input_tokens": 0,
                    "cache_creation": {"ephemeral_5m_input_tokens": 0,
                                       "ephemeral_1h_input_tokens": 0}}},
            }) + "\n")


def test_end_to_end_scan_and_cost():
    """Scan a synthetic log via the real CLI and check totals + the warning."""
    tmp = tempfile.mkdtemp()
    _write_log(tmp, [
        ("r1", "claude-opus-5-5", 1_000_000, 0),     # $20
        ("r2", "claude-fable-5-1", 2_000_000, 0),    # $100
        ("r3", "claude-zzz-9", 1_000_000, 0),        # opus fallback $20 + warning
        ("r4", "claude-haiku-5-5", 0, 200_000),      # long tier: $0.10
    ])
    env = dict(os.environ, HOME=tmp)
    p = subprocess.run([sys.executable, "cost.py"], cwd=HERE, env=env,
                       capture_output=True, text=True)
    out = json.loads(p.stdout)
    assert set(out["models"]) == {"claude-opus-5-5", "claude-fable-5-1",
                                  "claude-zzz-9", "claude-haiku-5-5:long"}
    assert "warning: unrecognized model 'claude-zzz-9'" in p.stderr
    assert "fable" not in p.stderr.lower()  # known entry must not warn
    cost_val = float(p.stderr.split("Estimated API cost: $")[1].replace(",", "").split()[0])
    assert abs(cost_val - 140) < 0.5, cost_val


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for fn in fns:
        fn()
        print(f"  ok  {fn.__name__}")
    print(f"\n{len(fns)} passed")
