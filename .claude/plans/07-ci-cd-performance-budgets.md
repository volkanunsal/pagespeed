# Plan: CI/CD Performance Budget Integration

## Context

The tool currently produces reports but has no built-in way to enforce performance standards. CI/CD pipelines need a pass/fail signal: run the analysis, compare results against a performance budget, and exit non-zero if thresholds are violated. This feature adds budget evaluation with CI-friendly exit codes, output formats, and optional webhook notifications — enabling automated, scheduled performance monitoring with alerting.

## Usage Examples

```bash
# Pipeline with budget enforcement (primary CI use case)
uv run pagespeed_insights_tool.py pipeline https://example.com/sitemap.xml \
    --budget budget.toml --sitemap-limit 20

# Use Google's CWV "good" thresholds as a built-in preset (no file needed)
uv run pagespeed_insights_tool.py pipeline https://example.com --budget cwv

# Evaluate existing results against a budget (no API calls)
uv run pagespeed_insights_tool.py budget results.csv --budget budget.toml

# CI with GitHub Actions annotations + webhook
uv run pagespeed_insights_tool.py pipeline https://example.com/sitemap.xml \
    --budget budget.toml --budget-format github --webhook https://hooks.slack.com/...
```

## Budget File Format (`budget.toml`)

```toml
[meta]
name = "Production budget"

[thresholds]
min_performance_score = 90
min_accessibility_score = 80
min_best_practices_score = 80
min_seo_score = 80
max_lcp_ms = 2500
max_cls = 0.1
max_tbt_ms = 200
max_fcp_ms = 1800
```

Each key maps to a DataFrame column via `BUDGET_METRIC_MAP`. Missing keys are skipped (not evaluated). The `cwv` built-in preset populates `max_*` keys from `CWV_THRESHOLDS["good"]` values.

## Exit Codes

| Code | Meaning |
|------|---------|
| 0 | Success / budget pass |
| 1 | Tool error (config, file not found, all URLs errored) |
| 2 | Budget fail (one or more URLs violated thresholds) |

Exit code 2 is a new constant `BUDGET_EXIT_CODE`. Existing command behavior is unchanged when `--budget` is not used.

## File to Modify

`pagespeed_insights_tool.py` (~1572 lines currently)

## Implementation Steps

### Step 1: Add budget constants and `BUDGET_METRIC_MAP` (after line 95)

New constants near `CWV_THRESHOLDS`:

```python
BUDGET_EXIT_CODE = 2

BUDGET_METRIC_MAP = {
    "min_performance_score":     ("performance_score",     ">="),
    "min_accessibility_score":   ("accessibility_score",   ">="),
    "min_best_practices_score":  ("best_practices_score",  ">="),
    "min_seo_score":             ("seo_score",             ">="),
    "max_lcp_ms":                ("lab_lcp_ms",            "<="),
    "max_cls":                   ("lab_cls",               "<="),
    "max_tbt_ms":                ("lab_tbt_ms",            "<="),
    "max_fcp_ms":                ("lab_fcp_ms",            "<="),
}

CWV_BUDGET_PRESET = {
    "max_lcp_ms": CWV_THRESHOLDS["lab_lcp_ms"]["good"],
    "max_cls":    CWV_THRESHOLDS["lab_cls"]["good"],
    "max_tbt_ms": CWV_THRESHOLDS["lab_tbt_ms"]["good"],
    "max_fcp_ms": CWV_THRESHOLDS["lab_fcp_ms"]["good"],
}
```

### Step 2: Add `load_budget()` function (near `load_config()`, ~line 124)

```python
def load_budget(budget_source: str) -> dict:
```

- If `budget_source == "cwv"`, return `{"thresholds": CWV_BUDGET_PRESET, "meta": {"name": "Core Web Vitals"}}`.
- Otherwise, read as TOML file. Exit 1 on missing file or parse error (matching `load_config()` pattern).
- Returns parsed dict with `thresholds` and optional `meta` sections.

### Step 3: Add `evaluate_budget()` function (new section after `_print_audit_summary`, ~line 790)

```python
def evaluate_budget(dataframe: pd.DataFrame, budget: dict) -> dict:
```

Logic:
1. Extract `thresholds` from budget dict.
2. Filter to rows where `error` is null/NaN.
3. For each row, check each threshold in `BUDGET_METRIC_MAP` — skip if the metric column is missing from the DataFrame or the row has NaN for that metric.
4. Build violation list per (url, strategy) pair: `{"metric", "actual", "threshold", "operator"}`.
5. Return verdict dict:

```python
{
    "budget_name": "Production budget",
    "verdict": "fail",      # "pass" | "fail" | "error"
    "passed": 5, "failed": 2, "total": 7, "errors_skipped": 1,
    "results": [
        {"url": "...", "strategy": "mobile", "verdict": "pass", "violations": []},
        {"url": "...", "strategy": "mobile", "verdict": "fail", "violations": [...]},
    ]
}
```

Edge cases:
- All URLs errored → `verdict = "error"`, exit code 1, warning printed.
- Partial errors → evaluate only successful rows, note `errors_skipped` count.
- Empty `[thresholds]` → all pass (no checks), warning printed.

### Step 4: Add CI output formatters (after `evaluate_budget`)

Three functions:

**`format_budget_text(verdict) -> str`** — human-readable summary for terminal/logs:
```
Budget: Production budget
Result: FAIL (5 passed, 2 failed, 7 total, 1 skipped)

FAIL  https://example.com/blog (mobile)
      performance_score: 72 (threshold: >= 90)
      lab_lcp_ms: 3200 (threshold: <= 2500)

PASS  https://example.com (mobile)
```

**`format_budget_json(verdict) -> str`** — JSON dump of verdict dict.

**`format_budget_github(verdict) -> str`** — GitHub Actions `::error` annotations:
```
::error::Budget FAIL: https://example.com/blog (mobile) — performance_score=72 (>= 90)
```

Auto-detection: if `GITHUB_ACTIONS` env var is set and `--budget-format` wasn't explicitly specified, default to `"github"`.

### Step 5: Add `send_budget_webhook()` (after formatters)

```python
def send_budget_webhook(webhook_url: str, verdict: dict) -> None:
```

Simple `requests.post(url, json=verdict, timeout=30)`. On failure: print warning to stderr. Never affects exit code — webhook errors are warnings only.

### Step 6: Add `_apply_budget()` orchestration helper

```python
def _apply_budget(dataframe: pd.DataFrame, args: argparse.Namespace) -> int:
```

Orchestrates budget flow when `--budget` is set:
1. `load_budget(args.budget)`
2. `evaluate_budget(dataframe, budget)`
3. Pick format (explicit `--budget-format` > auto-detect GitHub Actions > `"text"`)
4. Print formatted output to stderr
5. Send webhook if `--webhook` is set (respect `--webhook-on`)
6. Return exit code: 0 if pass, `BUDGET_EXIT_CODE` if fail, 1 if error

### Step 7: Add `budget` subcommand parser (in `build_argument_parser()`, after pipeline parser)

```
budget INPUT_FILE --budget FILE [--budget-format text|json|github] [--webhook URL] [--webhook-on always|fail]
```

### Step 8: Add budget-related flags to `pipeline`, `audit`, and `run` parsers

Add `--budget`, `--budget-format`, `--webhook`, `--webhook-on` to all three parsers. These are optional — behavior is unchanged when not used.

### Step 9: Add `cmd_budget()` handler

```python
def cmd_budget(args: argparse.Namespace) -> None:
```

Loads results via `load_report()` (reuse existing function at line 925), then calls `_apply_budget()` and `sys.exit()` with the returned code.

### Step 10: Integrate budget into `cmd_audit()` and `cmd_pipeline()`

At the end of each handler, after summary/report generation:

```python
if getattr(args, "budget", None):
    exit_code = _apply_budget(dataframe, args)
    sys.exit(exit_code)
```

`cmd_run()` already delegates to `cmd_audit()` — no separate change needed.

### Step 11: Register `budget` in dispatch table and update config

- Add `"budget": cmd_budget` to the `commands` dict in `main()`.
- Add budget-related keys to `config_key_map` in `apply_profile()`: `budget`, `budget_format`, `webhook_url`, `webhook_on`.

### Step 12: Add tests

New test classes in `test_pagespeed_insights_tool.py`:

| Class | Tests | Coverage |
|-------|-------|----------|
| `TestLoadBudget` | 4 | TOML parsing, `cwv` preset, file not found, malformed TOML |
| `TestEvaluateBudget` | 7 | All pass, one fail, multiple violations, missing metric skipped, all errors, partial errors, empty thresholds |
| `TestFormatBudget` | 5 | Text pass/fail, JSON structure, GitHub annotations format |
| `TestSendBudgetWebhook` | 2 | Success (mock), failure warning (mock) |
| `TestApplyBudget` | 3 | No budget → 0, pass → 0, fail → 2 |
| `TestBudgetParser` | 2 | `budget` subcommand parses, `--budget` on pipeline parses |

~24 new tests, ~120 lines.

### Step 13: Create `docs/ci-cd-performance-budgets.md` documentation

Create a comprehensive guide at `docs/ci-cd-performance-budgets.md` covering:

1. **Overview** — What performance budgets are and why CI/CD integration matters.
2. **Quick Start** — Minimal steps to get budget checking working (create `budget.toml`, run with `--budget`).
3. **Budget File Reference** — Complete list of all supported threshold keys with descriptions, types, and defaults. Explain the `min_*`/`max_*` naming convention. Document the `[meta]` section.
4. **Built-in Presets** — Explain the `--budget cwv` shorthand and what thresholds it applies.
5. **CLI Reference** — Document all budget-related flags (`--budget`, `--budget-format`, `--webhook`, `--webhook-on`) with examples for each.
6. **Exit Codes** — Table of exit codes (0, 1, 2) and what they mean for CI scripting.
7. **CI/CD Integration Examples**:
   - **GitHub Actions** — Complete workflow YAML with scheduled cron trigger (daily/weekly), `PAGESPEED_API_KEY` secret, budget checking, artifact upload of HTML reports, and failure notifications.
   - **GitLab CI** — `.gitlab-ci.yml` example with scheduled pipeline.
   - **Generic CI** — Shell script pattern that works in any CI system.
8. **Webhook Notifications** — How to set up webhooks for Slack (with incoming webhook URL format), Discord, and generic HTTP endpoints. Show example JSON payload.
9. **Configuration File Integration** — How to set budget defaults in `pagespeed.toml` under `[settings]` to avoid repeating CLI flags.
10. **The `budget` Subcommand** — How to evaluate existing results without re-running the API (useful for re-checking old data against new thresholds).
11. **Troubleshooting** — Common issues (API quota, all URLs erroring, empty thresholds).

### Step 14: Update CLAUDE.md

Add `budget` to the running examples and mention budget evaluation in the architecture section.

## Files Changed

| File | Change |
|------|--------|
| `pagespeed_insights_tool.py` | ~280 lines new + ~30 lines modified |
| `test_pagespeed_insights_tool.py` | ~120 lines new (24 tests across 6 classes) |
| `docs/ci-cd-performance-budgets.md` | ~200 lines new (comprehensive usage guide) |
| `CLAUDE.md` | ~10 lines updated (examples + architecture) |

## Verification

```bash
# 1. Unit tests pass
uv run test_pagespeed_insights_tool.py -v

# 2. Help text shows budget subcommand and --budget flag
uv run pagespeed_insights_tool.py budget --help
uv run pagespeed_insights_tool.py pipeline --help | grep budget

# 3. Budget subcommand evaluates existing results (use a saved JSON/CSV)
uv run pagespeed_insights_tool.py budget reports/sample.json --budget budget.toml

# 4. Pipeline with CWV preset (live API call, needs key)
uv run pagespeed_insights_tool.py pipeline https://example.com --budget cwv

# 5. Exit code is 2 on budget failure
uv run pagespeed_insights_tool.py budget reports/sample.json --budget budget.toml; echo "Exit: $?"

# 6. GitHub Actions format
uv run pagespeed_insights_tool.py budget reports/sample.json --budget budget.toml --budget-format github

# 7. Existing commands unaffected (regression)
uv run pagespeed_insights_tool.py pipeline --help
uv run pagespeed_insights_tool.py audit --help
```
