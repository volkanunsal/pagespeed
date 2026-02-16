# CI/CD Performance Budgets

## Overview

Performance budgets define measurable thresholds for web performance metrics. By integrating budget evaluation into CI/CD pipelines, you can automatically detect regressions and enforce performance standards on every deployment.

The `pagespeed_insights_tool.py` supports budget evaluation with:
- Pass/fail exit codes for CI gating
- Multiple output formats (text, JSON, GitHub Actions annotations)
- Webhook notifications for alerting (Slack, Discord, etc.)
- A built-in Core Web Vitals preset (`--budget cwv`)

## Quick Start

1. Create a `budget.toml` file:

```toml
[meta]
name = "Production budget"

[thresholds]
min_performance_score = 90
max_lcp_ms = 2500
max_cls = 0.1
max_tbt_ms = 200
```

2. Run with budget enforcement:

```bash
uv run pagespeed_insights_tool.py pipeline https://example.com --budget budget.toml
echo "Exit code: $?"  # 0 = pass, 2 = fail
```

Or use the built-in CWV preset (no file needed):

```bash
uv run pagespeed_insights_tool.py pipeline https://example.com --budget cwv
```

## Budget File Reference

Budget files use [TOML format](https://toml.io/) with two sections:

### `[meta]` (optional)

| Key | Type | Description |
|-----|------|-------------|
| `name` | string | Human-readable budget name, shown in reports |

### `[thresholds]`

Each key maps to a metric column. Prefix convention:
- `min_*` — metric must be **greater than or equal to** the value
- `max_*` — metric must be **less than or equal to** the value

Missing keys are skipped (not evaluated). Only define what you care about.

| Key | Metric Column | Type | Description |
|-----|---------------|------|-------------|
| `min_performance_score` | `performance_score` | int (0-100) | Lighthouse performance score |
| `min_accessibility_score` | `accessibility_score` | int (0-100) | Lighthouse accessibility score |
| `min_best_practices_score` | `best_practices_score` | int (0-100) | Lighthouse best practices score |
| `min_seo_score` | `seo_score` | int (0-100) | Lighthouse SEO score |
| `max_lcp_ms` | `lab_lcp_ms` | int (ms) | Largest Contentful Paint |
| `max_cls` | `lab_cls` | float | Cumulative Layout Shift |
| `max_tbt_ms` | `lab_tbt_ms` | int (ms) | Total Blocking Time |
| `max_fcp_ms` | `lab_fcp_ms` | int (ms) | First Contentful Paint |

## Built-in Presets

### `cwv` — Core Web Vitals

Use `--budget cwv` to apply Google's "good" CWV thresholds without a file:

| Threshold | Value |
|-----------|-------|
| `max_lcp_ms` | 2500 |
| `max_cls` | 0.1 |
| `max_tbt_ms` | 200 |
| `max_fcp_ms` | 1800 |

These values come from the `CWV_THRESHOLDS` constants in the tool.

## CLI Reference

### Budget-related flags

These flags work on `pipeline`, `audit`, and `run` subcommands:

| Flag | Default | Description |
|------|---------|-------------|
| `--budget FILE` | (none) | Budget TOML file or `cwv` preset. Enables budget evaluation. |
| `--budget-format FORMAT` | `text` | Output format: `text`, `json`, or `github` |
| `--webhook URL` | (none) | HTTP endpoint to POST the verdict JSON |
| `--webhook-on WHEN` | `always` | When to send: `always` or `fail` |

### Examples

```bash
# Pipeline with budget
uv run pagespeed_insights_tool.py pipeline https://example.com/sitemap.xml \
    --budget budget.toml --sitemap-limit 20

# Audit with CWV preset and JSON output
uv run pagespeed_insights_tool.py audit -f urls.txt --budget cwv --budget-format json

# Pipeline with GitHub annotations and Slack webhook
uv run pagespeed_insights_tool.py pipeline https://example.com \
    --budget budget.toml --budget-format github \
    --webhook https://hooks.slack.com/services/T.../B.../xxx
```

## Exit Codes

| Code | Meaning |
|------|---------|
| 0 | Success — budget passed (or no budget specified) |
| 1 | Tool error — config issue, file not found, all URLs errored |
| 2 | Budget fail — one or more URLs violated thresholds |

In CI scripts, use the exit code to gate deployments:

```bash
uv run pagespeed_insights_tool.py pipeline https://example.com --budget budget.toml
if [ $? -eq 2 ]; then
    echo "Performance budget exceeded — blocking deployment"
    exit 1
fi
```

## CI/CD Integration Examples

### GitHub Actions

```yaml
name: Performance Budget Check
on:
  schedule:
    - cron: '0 6 * * 1'  # Weekly on Monday at 6am UTC
  workflow_dispatch:

jobs:
  performance-check:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4

      - name: Install uv
        uses: astral-sh/setup-uv@v3

      - name: Run performance audit
        env:
          PAGESPEED_API_KEY: ${{ secrets.PAGESPEED_API_KEY }}
        run: |
          uv run pagespeed_insights_tool.py pipeline \
            https://example.com/sitemap.xml \
            --budget budget.toml \
            --budget-format github \
            --sitemap-limit 20 \
            --output-dir ./reports

      - name: Upload reports
        if: always()
        uses: actions/upload-artifact@v4
        with:
          name: pagespeed-reports
          path: ./reports/
```

The `--budget-format github` flag is auto-detected when running in GitHub Actions (via the `GITHUB_ACTIONS` environment variable), so you can omit it if you prefer.

### GitLab CI

```yaml
performance-check:
  image: python:3.13
  rules:
    - if: $CI_PIPELINE_SOURCE == "schedule"
  before_script:
    - pip install uv
  script:
    - uv run pagespeed_insights_tool.py pipeline
        https://example.com/sitemap.xml
        --budget budget.toml
        --sitemap-limit 20
        --output-dir ./reports
  artifacts:
    paths:
      - reports/
    when: always
  variables:
    PAGESPEED_API_KEY: $PAGESPEED_API_KEY
```

### Generic CI (Shell Script)

```bash
#!/usr/bin/env bash
set -euo pipefail

uv run pagespeed_insights_tool.py pipeline \
    https://example.com/sitemap.xml \
    --budget budget.toml \
    --budget-format json \
    --output-dir ./reports \
    --sitemap-limit 20

exit_code=$?
if [ $exit_code -eq 2 ]; then
    echo "FAIL: Performance budget exceeded"
    exit 1
elif [ $exit_code -ne 0 ]; then
    echo "ERROR: Tool failed with exit code $exit_code"
    exit 1
fi
echo "PASS: All URLs within budget"
```

## Webhook Notifications

When `--webhook URL` is set, the tool POSTs the full verdict JSON to the URL. Use `--webhook-on fail` to only send on failures.

### Slack (Incoming Webhook)

Slack incoming webhooks expect a `text` field. You can pipe the verdict through a transformer, or use a Slack workflow that accepts JSON. For simple setups, the raw JSON payload works with Slack's workflow builder.

### Discord

Discord webhooks accept JSON with a `content` field. Use a middleware service or serverless function to transform the verdict JSON.

### Example Verdict Payload

```json
{
  "budget_name": "Production budget",
  "verdict": "fail",
  "passed": 5,
  "failed": 2,
  "total": 7,
  "errors_skipped": 1,
  "results": [
    {
      "url": "https://example.com",
      "strategy": "mobile",
      "verdict": "pass",
      "violations": []
    },
    {
      "url": "https://example.com/blog",
      "strategy": "mobile",
      "verdict": "fail",
      "violations": [
        {
          "metric": "performance_score",
          "actual": 72,
          "threshold": 90,
          "operator": ">="
        }
      ]
    }
  ]
}
```

## Configuration File Integration

Set budget defaults in `pagespeed.toml` to avoid repeating CLI flags:

```toml
[settings]
budget = "budget.toml"
budget_format = "text"
webhook_url = "https://hooks.slack.com/services/..."
webhook_on = "fail"
```

Or per-profile:

```toml
[profiles.ci]
budget = "budget.toml"
budget_format = "github"
webhook_on = "fail"
```

Then run with `--profile ci`:

```bash
uv run pagespeed_insights_tool.py pipeline https://example.com --profile ci
```

## The `budget` Subcommand

Evaluate existing results without re-running the API:

```bash
# Re-check old results against new thresholds
uv run pagespeed_insights_tool.py budget reports/20260216T120000Z-mobile.csv --budget budget.toml

# Use JSON results
uv run pagespeed_insights_tool.py budget reports/20260216T120000Z-mobile.json --budget cwv

# With different output format
uv run pagespeed_insights_tool.py budget results.csv --budget budget.toml --budget-format json
```

This is useful for:
- Re-evaluating historical data against updated thresholds
- Testing budget configurations before deploying to CI
- Comparing different budget presets against the same data

## Troubleshooting

### API Quota Exceeded
Without an API key, Google limits requests to ~25/day. Set `PAGESPEED_API_KEY` as an environment variable or in `pagespeed.toml`.

### All URLs Errored
If every URL fails (network errors, API errors), the tool exits with code 1 and prints a warning. No budget evaluation occurs. Check your network, API key, and URL validity.

### Empty Thresholds
A budget file with an empty `[thresholds]` section means no checks are performed — all URLs pass by default. A warning is printed. Add at least one threshold key.

### Missing Metric Columns
If a threshold references a metric not present in the results (e.g., `max_lcp_ms` but `lab_lcp_ms` column is missing), that check is silently skipped for the affected rows. This can happen when Lighthouse doesn't return certain metrics.
