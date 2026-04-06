#!/usr/bin/env python3
"""
Claude Code Cost Scanner

Scans your local Claude Code JSONL files and calculates what your
usage would cost at API list prices. Outputs JSON to stdout.

Usage:
    python3 cost.py              # JSON to stdout
    python3 cost.py --clipboard  # also copy to clipboard
"""
import json, glob, os, sys, calendar, subprocess, platform
from collections import defaultdict
from datetime import datetime, timedelta

# From docs.anthropic.com/pricing — $/MTok
# i=input, o=output, cr=cache_read, c5=cache_write_5min, c1=cache_write_1hour
PRICING = {
    # Current
    "claude-opus-4-6":            {"i": 5,    "o": 25,   "cr": 0.50, "c5": 6.25,  "c1": 10},
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
            for k in ["i", "o", "cr", "c5", "c1"]:
                M[v["m"]][k] += v[k]

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
        r = PRICING.get(model)
        if not r:
            # Fuzzy match by model prefix
            for k, v in PRICING.items():
                if model.startswith(k.split("-20")[0]):
                    r = v
                    break
            if not r:
                r = PRICING["claude-opus-4-6"]
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
