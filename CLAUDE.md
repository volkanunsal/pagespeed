# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Single-file Python CLI tool (`pagespeed_insights_tool.py`) that batch-queries Google's PageSpeed Insights API v5 and outputs CSV/JSON/HTML reports. Uses PEP 723 inline script metadata — no `pyproject.toml` or project scaffolding.

## Running the Tool

All commands use `uv run` which auto-manages the virtualenv and dependencies:

```bash
uv run pagespeed_insights_tool.py --help
uv run pagespeed_insights_tool.py quick-check https://example.com
uv run pagespeed_insights_tool.py audit -f urls.txt --strategy both --output-format both
uv run pagespeed_insights_tool.py compare before.csv after.csv
uv run pagespeed_insights_tool.py report results.csv --open
```

There are no tests, no linter config, and no build step. The tool is the single `.py` file.

## Architecture

Everything is in `pagespeed_insights_tool.py` (~700 lines). Key sections in order:

1. **PEP 723 metadata block** (lines 1-7) — dependencies: `requests`, `pandas`
2. **Constants** — `LAB_METRICS`, `FIELD_METRICS`, `CWV_THRESHOLDS` are data-driven lists that control metric extraction and HTML report rendering. Add new metrics here.
3. **Config/Profile** — `load_config()` reads TOML, `apply_profile()` merges with CLI args. Resolution: CLI flags > profile > settings > built-in defaults. `TrackingAction` tracks which argparse flags were explicitly set.
4. **API Client** — `fetch_pagespeed_result()` with retry logic (exponential backoff on 429/500/503). Single function, returns raw API JSON.
5. **Metrics Extraction** — `extract_metrics()` walks the API response using the `LAB_METRICS`/`FIELD_METRICS` lists. Returns a flat dict per (url, strategy) pair.
6. **Batch Processing** — `process_urls()` uses `ThreadPoolExecutor` + `threading.Semaphore(1)` for rate-limited concurrency.
7. **Output Formatters** — `output_csv()`, `output_json()`, `generate_html_report()`. JSON wraps results in a metadata envelope.
8. **Subcommand Handlers** — `cmd_quick_check()`, `cmd_audit()`, `cmd_compare()`, `cmd_report()`, `cmd_run()`.

## Key Design Patterns

- **Data-driven metric extraction**: `LAB_METRICS` and `FIELD_METRICS` tuples map API paths to output column names. `extract_metrics()` iterates these lists — no per-metric code.
- **Config merging via TrackingAction**: Custom argparse actions record which flags were explicitly set on CLI, so `apply_profile()` only fills in unset values from config/profile.
- **Rate limiting**: Even with N workers, a shared `Semaphore(1)` + delay timer serializes actual API calls. Workers prepare results in parallel but HTTP requests are sequential.
- **Auto-timestamped output**: Files named `{YYYYMMDD}T{HHMMSS}Z-{strategy}.{ext}` — safe for cron, never overwrites.

## API Key

Set via `PAGESPEED_API_KEY` env var, `--api-key` flag, or `api_key` in `pagespeed.toml`. Without a key, quota is ~25 queries/day on Google's shared project.

## Plans

Plan files are stored in `./.claude/plans/`. Use sequential, semantic filenames following the pattern `NN-plan-name.md` (e.g., `01-add-lighthouse-support.md`, `02-refactor-output-formatters.md`). Increment the number based on existing files in the directory.

## Config File

Optional `pagespeed.toml` discovered in CWD or `~/.config/pagespeed/config.toml`. Supports `[settings]` defaults and `[profiles.name]` named profiles applied via `--profile`.
