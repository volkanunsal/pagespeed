# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Single-file Python CLI tool (`pagespeed_insights_tool.py`) that batch-queries Google's PageSpeed Insights API v5 and outputs CSV/JSON/HTML reports. Published to PyPI as `pagespeed` and also supports PEP 723 inline script metadata for direct `uv run` usage.

## Running the Tool

When installed via `pip install pagespeed` or run via `uvx pagespeed`:

```bash
pagespeed --help
pagespeed quick-check https://example.com
pagespeed audit -f urls.txt --strategy both --output-format both
pagespeed pipeline https://example.com/sitemap.xml --sitemap-limit 20 --open
pagespeed compare before.csv after.csv
pagespeed report results.csv --open
pagespeed pipeline https://example.com --budget cwv
pagespeed budget results.csv --budget budget.toml
pagespeed quick-check https://example.com --runs 3
pagespeed audit -f urls.txt --runs 5 --strategy both
```

For local development, `uv run` still works via PEP 723 inline metadata:

```bash
uv run pagespeed_insights_tool.py quick-check https://example.com
```

## Architecture

Everything is in `pagespeed_insights_tool.py` (~700 lines). Key sections in order:

1. **PEP 723 metadata block** (lines 1-7) — dependencies: `requests`, `pandas`
2. **Constants** — `LAB_METRICS`, `FIELD_METRICS`, `CWV_THRESHOLDS` are data-driven lists that control metric extraction and HTML report rendering. Add new metrics here.
3. **Config/Profile** — `load_config()` reads TOML, `apply_profile()` merges with CLI args. Resolution: CLI flags > profile > settings > built-in defaults. `TrackingAction` tracks which argparse flags were explicitly set.
4. **API Client** — `fetch_pagespeed_result()` with retry logic (exponential backoff on 429/500/503). Single function, returns raw API JSON.
5. **Metrics Extraction** — `extract_metrics()` walks the API response using the `LAB_METRICS`/`FIELD_METRICS` lists. Returns a flat dict per (url, strategy) pair.
6. **Batch Processing** — `process_urls()` uses `ThreadPoolExecutor` + `threading.Semaphore(1)` for rate-limited concurrency. Supports multi-run via `--runs N`.
6b. **Multi-Run Aggregation** — `aggregate_multi_run()` groups by (url, strategy), computes median for numeric columns, mode for categories. `MEDIAN_ELIGIBLE_COLUMNS` controls which columns get aggregated.
7. **Output Formatters** — `output_csv()`, `output_json()`, `generate_html_report()`. JSON wraps results in a metadata envelope.
8. **Budget Evaluation** — `load_budget()`, `evaluate_budget()`, CI output formatters (`format_budget_text/json/github`), `send_budget_webhook()`, `_apply_budget()` orchestration. Exit code 2 on budget failure.
9. **Subcommand Handlers** — `cmd_quick_check()`, `cmd_audit()`, `cmd_compare()`, `cmd_report()`, `cmd_run()`, `cmd_pipeline()`, `cmd_budget()`.

## Key Design Patterns

- **Data-driven metric extraction**: `LAB_METRICS` and `FIELD_METRICS` tuples map API paths to output column names. `extract_metrics()` iterates these lists — no per-metric code.
- **Config merging via TrackingAction**: Custom argparse actions record which flags were explicitly set on CLI, so `apply_profile()` only fills in unset values from config/profile.
- **Rate limiting**: A shared `Semaphore(1)` gates API calls so each worker sleeps `delay` seconds after the previous request before starting its own. HTTP requests are concurrent-but-staggered: multiple calls can be in-flight simultaneously, but they start `delay` seconds apart. Completions therefore also arrive `delay` seconds apart, which looks sequential in the progress display even though total wall time is much shorter than pure sequential processing.
- **Auto-timestamped output**: Files named `{YYYYMMDD}T{HHMMSS}Z-{strategy}.{ext}` — safe for cron, never overwrites.
- **Multi-run median scoring**: `--runs N` runs each URL N times with interleaved ordering (run 1 all URLs, run 2 all URLs, etc.) and computes median values. Adds `runs_completed`, `score_range`, `score_stddev` metadata columns when N > 1.

## API Key

Set via `PAGESPEED_API_KEY` env var, `--api-key` flag, or `api_key` in `pagespeed.toml`. Without a key, quota is ~25 queries/day on Google's shared project.

## Plans

Plan files are stored in `./.claude/plans/`. Use sequential, semantic filenames following the pattern `NN-plan-name.md` (e.g., `01-add-lighthouse-support.md`, `02-refactor-output-formatters.md`). Increment the number based on existing files in the directory.

Every plan must include a final step to run the test suite (`uv run pytest test_pagespeed_insights_tool.py -v`) and confirm all tests pass before considering the work complete. This guards against regressions introduced by the changes.

## Worktree Workflow

See `.claude/memory/worktree-workflow.md` for the full workflow, naming conventions, lifecycle steps, and rules.

## Config File

Optional `pagespeed.toml` discovered in CWD or `~/.config/pagespeed/config.toml`. Supports `[settings]` defaults and `[profiles.name]` named profiles applied via `--profile`.

## Distribution

- **PyPI package**: `pagespeed` — installs a `pagespeed` console script
- **Console script entry point**: `pagespeed` → `pagespeed_insights_tool:main`
- **Dual-mode compatibility**: The tool works both as an installed package (`pagespeed` command) and as a PEP 723 script (`uv run pagespeed_insights_tool.py`). The `# /// script` block at the top is ignored by hatchling but enables direct `uv run` usage.
- **Dependency sync**: Dependencies are declared in two places — `pyproject.toml` `[project.dependencies]` and the PEP 723 `# /// script` block. Both must be kept in sync when adding/removing dependencies.
- **Version**: Defined as `__version__` in `pagespeed_insights_tool.py`. Hatch reads it dynamically via `[tool.hatch.version]`.
- **Build**: `uv build` produces sdist + wheel in `dist/`. Only `pagespeed_insights_tool.py` is included (controlled by `[tool.hatch.build] include`).
- **Release process**: Bump `__version__` in `pagespeed_insights_tool.py`, commit, tag `vX.Y.Z`, push tag. The `publish.yml` workflow handles test → build → PyPI publish → GitHub Release.
