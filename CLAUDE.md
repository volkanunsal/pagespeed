# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Single-file Python CLI tool (`pagespeed_insights_tool.py`) that batch-queries Google's PageSpeed Insights API v5 and outputs CSV/JSON/HTML reports. Published to PyPI as `pagespeed-insights` and also supports PEP 723 inline script metadata for direct `uv run` usage.

## Running the Tool

When installed via `pip install pagespeed-insights` or run via `uvx pagespeed-insights`:

```bash
pagespeed --help
pagespeed quick-check https://example.com
pagespeed audit -f urls.txt --strategy both --output-format both
pagespeed pipeline https://example.com/sitemap.xml --sitemap-limit 20 --open
pagespeed compare before.csv after.csv
pagespeed report results.csv --open
pagespeed pipeline https://example.com --budget cwv
pagespeed budget results.csv --budget budget.toml
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
6. **Batch Processing** — `process_urls()` uses `ThreadPoolExecutor` + `threading.Semaphore(1)` for rate-limited concurrency.
7. **Output Formatters** — `output_csv()`, `output_json()`, `generate_html_report()`. JSON wraps results in a metadata envelope.
8. **Budget Evaluation** — `load_budget()`, `evaluate_budget()`, CI output formatters (`format_budget_text/json/github`), `send_budget_webhook()`, `_apply_budget()` orchestration. Exit code 2 on budget failure.
9. **Subcommand Handlers** — `cmd_quick_check()`, `cmd_audit()`, `cmd_compare()`, `cmd_report()`, `cmd_run()`, `cmd_pipeline()`, `cmd_budget()`.

## Key Design Patterns

- **Data-driven metric extraction**: `LAB_METRICS` and `FIELD_METRICS` tuples map API paths to output column names. `extract_metrics()` iterates these lists — no per-metric code.
- **Config merging via TrackingAction**: Custom argparse actions record which flags were explicitly set on CLI, so `apply_profile()` only fills in unset values from config/profile.
- **Rate limiting**: Even with N workers, a shared `Semaphore(1)` + delay timer serializes actual API calls. Workers prepare results in parallel but HTTP requests are sequential.
- **Auto-timestamped output**: Files named `{YYYYMMDD}T{HHMMSS}Z-{strategy}.{ext}` — safe for cron, never overwrites.

## API Key

Set via `PAGESPEED_API_KEY` env var, `--api-key` flag, or `api_key` in `pagespeed.toml`. Without a key, quota is ~25 queries/day on Google's shared project.

## Plans

Plan files are stored in `./.claude/plans/`. Use sequential, semantic filenames following the pattern `NN-plan-name.md` (e.g., `01-add-lighthouse-support.md`, `02-refactor-output-formatters.md`). Increment the number based on existing files in the directory.

## Worktree Workflow

Required before implementing any plan. Not required for small ad-hoc changes (typos, config edits) — those can go directly on `main`.

### Naming

Worktree directories live under `/tmp/worktrees/`. Names use a 3-digit zero-padded sequential number + kebab-case description (e.g., `001-add-lighthouse-support`). The branch name matches the directory name. Determine the next number by inspecting existing branches:

```bash
git branch --list '[0-9]*'
```

### Lifecycle

1. **Pre-flight** — Verify the working tree is clean:
   ```bash
   git status --porcelain
   ```
   If output is non-empty, ask the user to stash, commit, or discard before proceeding.

2. **Create** — Create the worktree and branch from `main`:
   ```bash
   mkdir -p /tmp/worktrees
   git worktree add -b NNN-name /tmp/worktrees/NNN-name main
   ```

3. **Implement** — All file operations use absolute paths into the worktree. Commits go through `git -C`:
   ```bash
   git -C /tmp/worktrees/NNN-name add ...
   git -C /tmp/worktrees/NNN-name commit -m "..."
   ```
   Plans are read from the main working directory; worktrees are for implementation only.

4. **Merge** — From the main working directory, merge the branch (regular merge, not squash, to preserve per-step commit history):
   ```bash
   git merge NNN-name
   ```

5. **Commit plan** — After merging, commit the plan file to `main`:
   ```bash
   git add .claude/plans/NN-plan-name.md
   git commit -m "docs: add plan for ..."
   ```

6. **Cleanup** — Remove the worktree and delete the branch:
   ```bash
   git worktree remove /tmp/worktrees/NNN-name
   git branch -d NNN-name
   ```

### Rules

- Never commit plan implementation directly to `main`.
- One worktree per plan.
- Clean up stale worktrees before creating new ones: `git worktree remove --force /tmp/worktrees/NNN-name`.
- All branches stay local — no `git push` to remote.

## Config File

Optional `pagespeed.toml` discovered in CWD or `~/.config/pagespeed/config.toml`. Supports `[settings]` defaults and `[profiles.name]` named profiles applied via `--profile`.

## Distribution

- **PyPI package**: `pagespeed-insights` — installs a `pagespeed` console script
- **Console script entry point**: `pagespeed` → `pagespeed_insights_tool:main`
- **Dual-mode compatibility**: The tool works both as an installed package (`pagespeed` command) and as a PEP 723 script (`uv run pagespeed_insights_tool.py`). The `# /// script` block at the top is ignored by hatchling but enables direct `uv run` usage.
- **Dependency sync**: Dependencies are declared in two places — `pyproject.toml` `[project.dependencies]` and the PEP 723 `# /// script` block. Both must be kept in sync when adding/removing dependencies.
- **Version**: Defined as `__version__` in `pagespeed_insights_tool.py`. Hatch reads it dynamically via `[tool.hatch.version]`.
- **Build**: `uv build` produces sdist + wheel in `dist/`. Only `pagespeed_insights_tool.py` is included (controlled by `[tool.hatch.build] include`).
- **Release process**: Bump `__version__` in `pagespeed_insights_tool.py`, commit, tag `vX.Y.Z`, push tag. The `publish.yml` workflow handles test → build → PyPI publish → GitHub Release.
