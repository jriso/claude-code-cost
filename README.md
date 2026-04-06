# Claude Code Cost Calculator

How much would your Claude Code usage cost at API list prices?

Scans your local `~/.claude/projects/` JSONL files, deduplicates by `requestId`, and calculates the cost using [Anthropic's published pricing](https://docs.anthropic.com/en/docs/about-claude/pricing).

## Quick start

```bash
python3 <(curl -sL https://raw.githubusercontent.com/jriso/claude-code-cost/main/cost.py)
```

Outputs a JSON summary to stdout (~7 seconds). Paste it into the calculator at [risogroup.co/projects/claude-cost](https://risogroup.co/projects/claude-cost) to see a visual breakdown.

## What it does

- Reads JSONL session logs that Claude Code writes locally
- Deduplicates streaming events by `requestId` (avoids ~1.5x overcounting)
- Splits cache writes by tier (5-minute vs 1-hour) using `cache_creation.ephemeral_*` fields
- Applies per-model pricing for 13+ model versions
- Reports the last complete calendar month

## What it doesn't do

- Access any API or send data anywhere
- Read anything outside `~/.claude/projects/`
- Capture claude.ai web or mobile app usage (Claude Code only)

## Options

```bash
python3 cost.py              # JSON to stdout
python3 cost.py --clipboard  # also copy to clipboard (macOS/Linux)
```

## Methodology

See the [Sources & methodology](https://risogroup.co/projects/claude-cost) section on the calculator page, or review `cost.py` directly — it's ~100 lines of stdlib Python.
