#!/usr/bin/env python3
"""
Claude Code Cost Scanner

Scans your local Claude Code JSONL files and calculates what your
usage would cost at API list prices. Outputs JSON to stdout.

Usage:
    python3 cost.py              # JSON to stdout
    python3 cost.py --clipboard  # also copy to clipboard
"""
import json, glob, os, re, sys, calendar, subprocess, platform
from collections import defaultdict
from datetime import datetime, timedelta

# From platform.claude.com/docs/en/about-claude/pricing — $/MTok
# i=input, o=output, cr=cache_read, c5=cache_write_5min, c1=cache_write_1hour
PRICING = {
    # Current
    "claude-fable-5-1":           {"i": 10,   "o": 50,   "cr": 0.25, "c5": 12.50, "c1": 20},
    "claude-mythos-5-1":          {"i": 10,   "o": 50,   "cr": 0.25, "c5": 12.50, "c1": 20},
    "claude-opus-5-5":            {"i": 4,    "o": 20,   "cr": 0.20, "c5": 5,     "c1": 8},
    "claude-sonnet-5-5":          {"i": 2,    "o": 10,   "cr": 0.10, "c5": 2.50,  "c1": 4},
    "claude-haiku-5-5":           {"i": 0.10, "o": 0.50, "cr": 0.01, "c5": 0.125, "c1": 0.20},
    "claude-haiku-5-5:long":      {"i": 0.50, "o": 2.50, "cr": 0.05, "c5": 0.625, "c1": 1},
    # Previous
    "claude-fable-5":             {"i": 10,   "o": 50,   "cr": 1.00, "c5": 12.50, "c1": 20},
    "claude-mythos-5":            {"i": 10,   "o": 50,   "cr": 1.00, "c5": 12.50, "c1": 20},
    "claude-opus-5":              {"i": 5,    "o": 25,   "cr": 0.50, "c5": 6.25,  "c1": 10},
    "claude-opus-4-8":            {"i": 5,    "o": 25,   "cr": 0.50, "c5": 6.25,  "c1": 10},
    "claude-opus-4-7":            {"i": 5,    "o": 25,   "cr": 0.50, "c5": 6.25,  "c1": 10},
    "claude-opus-4-6":            {"i": 5,    "o": 25,   "cr": 0.50, "c5": 6.25,  "c1": 10},
    "claude-sonnet-5":            {"i": 2,    "o": 10,   "cr": 0.20, "c5": 2.50,  "c1": 4},
    "claude-sonnet-4-6":          {"i": 3,    "o": 15,   "cr": 0.30, "c5": 3.75,  "c1": 6},
    "claude-haiku-4-5-20251001":  {"i": 1,    "o": 5,    "cr": 0.10, "c5": 1.25,  "c1": 2},
    # Legacy
    "claude-opus-4-5-20251101":   {"i": 5,    "o": 25,   "cr": 0.50, "c5": 6.25,  "c1": 10},
    "claude-opus-4-1-20250805":   {"i": 15,   "o": 75,   "cr": 1.50, "c5": 18.75, "c1": 30},
    "claude-sonnet-4-5-20250929": {"i": 3,    "o": 15,   "cr": 0.30, "c5": 3.75,  "c1": 6},
    "claude-sonnet-4-20250514":   {"i": 3,    "o": 15,   "cr": 0.30, "c5": 3.75,  "c1": 6},
    "claude-opus-4-20250514":     {"i": 15,   "o": 75,   "cr": 1.50, "c5": 18.75, "c1": 30},
    "claude-haiku-3-5-20241022":  {"i": 0.80, "o": 4,    "cr": 0.08, "c5": 1,     "c1": 1.6},
    "claude-3-haiku-20240307":    {"i": 0.25, "o": 1.25, "cr": 0.03, "c5": 0.30,  "c1": 0.50},
    "claude-3-opus-20240229":     {"i": 15,   "o": 75,   "cr": 1.50, "c5": 18.75, "c1": 30},
    "claude-3-5-sonnet-20241022": {"i": 3,    "o": 15,   "cr": 0.30, "c5": 3.75,  "c1": 6},
    "claude-3-5-sonnet-20240620": {"i": 3,    "o": 15,   "cr": 0.30, "c5": 3.75,  "c1": 6},
}

# Models priced higher above a prompt-size threshold. Requests over the
# threshold are tallied under "<model>:long".
LONG_PROMPT = {"claude-haiku-5-5": 100_000}

# Current-generation list rates by family, used for models not in PRICING.
# Prevents an unknown future model from silently inheriting a legacy
# (higher-priced) entry via prefix matching.
DEFAULT_RATES = {
    "fable":  PRICING["claude-fable-5-1"],
    "mythos": PRICING["claude-mythos-5-1"],
    "opus":   PRICING["claude-opus-5-5"],
    "sonnet": PRICING["claude-sonnet-5-5"],
    "haiku":  PRICING["claude-haiku-5-5"],
}

DATED = re.compile(r"^(.+)-\d{8}$")

def base_model(model):
    """Strip a dated pin (claude-opus-5-5-20261201 -> claude-opus-5-5) if the
    undated alias is known. Requires an 8-digit date so e.g. claude-opus-5-5
    can never resolve to claude-opus-5."""
    m = DATED.match(model)
    return m.group(1) if m and m.group(1) in PRICING else model

def bucket(model, prompt_tokens):
    """Key a request is tallied under in the JSON output."""
    limit = LONG_PROMPT.get(base_model(model))
    return model + ":long" if limit and prompt_tokens > limit else model

def rate_for(model):
    """Resolve a model id to a rate dict. Exact match first, then a dated
    variant of a known alias, then the current-gen default for the family
    with a warning. Never silently falls through to a legacy entry."""
    name, _, tier = model.partition(":")
    key = base_model(name) + (":" + tier if tier else "")
    r = PRICING.get(key) or PRICING.get(base_model(name))
    if r:
        return r
    seg = name.split("-")
    for family, v in DEFAULT_RATES.items():
        if family in seg:
            print(f"warning: unknown model '{model}', using current {family} list rates",
                  file=sys.stderr)
            return v
    print(f"warning: unrecognized model '{model}', using opus list rates", file=sys.stderr)
    return DEFAULT_RATES["opus"]

def main():
    clipboard = "--clipboard" in sys.argv or "-c" in sys.argv

    print("Scanning ~/.claude/projects/ ...", file=sys.stderr, flush=True)

    R = {}
    sessions = set()
    file_count = 0
    for f in glob.glob(os.path.expanduser("~/.claude/projects/**/*.jsonl"), recursive=True):
        file_count += 1
        with open(f) as h:
            for line in h:
                try:
                    d = json.loads(line)
                except Exception:
                    continue
                sid = d.get("sessionId", "")
                ts = d.get("timestamp", "")
                if d.get("type") != "assistant" or "message" not in d:
                    continue
                msg = d["message"]
                if not isinstance(msg, dict) or msg.get("model", "").startswith("<"):
                    continue
                u = msg.get("usage", {})
                cc = u.get("cache_creation", {})
                R[d.get("requestId", "")] = {
                    "d": ts[:10], "m": msg.get("model", ""),
                    "s": sid,
                    "i": u.get("input_tokens", 0),
                    "o": u.get("output_tokens", 0),
                    "cr": u.get("cache_read_input_tokens", 0),
                    "c5": cc.get("ephemeral_5m_input_tokens", 0),
                    "c1": cc.get("ephemeral_1h_input_tokens", 0),
                }

    # Last complete month
    t = datetime.now()
    cutoff = (t.replace(day=1) - timedelta(days=1)).replace(day=1).strftime("%Y-%m-%d")
    end = t.replace(day=1).strftime("%Y-%m-%d")
    mo = cutoff[:7]
    md = calendar.monthrange(int(mo[:4]), int(mo[5:7]))[1]

    M = defaultdict(lambda: {"i": 0, "o": 0, "cr": 0, "c5": 0, "c1": 0})
    days = set()
    month_sessions = set()
    req_count = 0
    for v in R.values():
        if cutoff <= v["d"] < end:
            days.add(v["d"])
            req_count += 1
            if v.get("s"):
                month_sessions.add(v["s"])
            key = bucket(v["m"], v["i"] + v["cr"] + v["c5"] + v["c1"])
            for k in ["i", "o", "cr", "c5", "c1"]:
                M[key][k] += v[k]

    result = {
        "month": mo,
        "days": len(days),
        "month_days": md,
        "models": dict(M),
        "requests": req_count,
        "sessions": len(month_sessions),
    }

    # Compute cost for stderr summary (same math as the web UI)
    total = 0
    for model, tokens in M.items():
        r = rate_for(model)
        total += (tokens["i"] / 1e6 * r["i"] + tokens["o"] / 1e6 * r["o"]
                  + tokens["cr"] / 1e6 * r["cr"]
                  + tokens["c5"] / 1e6 * r["c5"] + tokens["c1"] / 1e6 * r["c1"])

    print(f"Scanned {file_count:,} files, {len(R):,} total requests", file=sys.stderr)
    print(f"Month: {mo} ({len(days)}/{md} days, {req_count:,} requests)", file=sys.stderr)
    print(f"Estimated API cost: ${total:,.0f}", file=sys.stderr)

    output = json.dumps(result)
    print(output)

    if clipboard:
        try:
            if platform.system() == "Darwin":
                subprocess.run(["pbcopy"], input=output.encode(), check=True)
            else:
                subprocess.run(["xclip", "-selection", "clipboard"], input=output.encode(), check=True)
            print("Copied to clipboard!", file=sys.stderr)
        except Exception:
            print("Could not copy to clipboard. Paste the JSON above.", file=sys.stderr)

if __name__ == "__main__":
    main()
