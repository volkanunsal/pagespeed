# Architecture: PageSpeed Insights Batch Analysis Tool

## Project Overview

### Problem

Checking PageSpeed Insights for multiple URLs is manual, slow, and doesn't produce structured data for tracking over time. Teams need a way to batch-analyze URLs, compare results across runs, and share visual reports.

### Goals

- Automate PageSpeed Insights analysis across multiple URLs
- Extract both lab (Lighthouse) and field (CrUX) performance data
- Output structured reports in CSV, JSON, and HTML formats
- Support concurrent processing with rate limiting
- Zero-setup experience via `uv run` with PEP 723 inline metadata

### Scope

Single-file CLI tool. No web server, no database, no persistent state beyond output files and optional TOML config.

## Design Decisions

### Single-file PEP 723 script (vs full package)

The tool is a single `pagespeed_insights_tool.py` file with inline dependency metadata:

```python
# /// script
# requires-python = ">=3.13"
# dependencies = ["requests", "pandas"]
# ///
```

**Why:** `uv run pagespeed_insights_tool.py` handles everything — resolves dependencies, creates a virtual environment, and runs the script. No `pyproject.toml`, no `pip install`, no project scaffolding. The tool can be shared as a single file.

**Trade-off:** All code in one file. This is acceptable at the current scale (~800 lines) but would warrant refactoring into a package if the tool grows significantly.

### argparse (vs click/typer)

**Why:** Standard library, no extra dependency. The CLI has 5 subcommands with straightforward flags — argparse handles this well. Custom `TrackingAction` classes track which flags were explicitly set, enabling clean config-file merging.

**Trade-off:** More boilerplate than click/typer, but avoids adding a dependency for something the stdlib handles.

### ThreadPoolExecutor (vs asyncio)

**Why:** The workload is I/O-bound (HTTP requests to Google's API). ThreadPoolExecutor is simpler to reason about and debug than asyncio, with comparable performance for this use case. A shared `threading.Semaphore` handles rate limiting.

**Trade-off:** Less efficient than asyncio for very high concurrency, but the API rate limits cap useful concurrency at ~4 workers anyway.

### TOML config (vs YAML/JSON)

**Why:** Python 3.11+ includes `tomllib` in the standard library. TOML is human-readable, supports comments, and is the standard for Python project configuration. No extra dependency needed.

### Both lab and field data

**Why:** Lab data (Lighthouse) gives consistent, reproducible synthetic results. Field data (CrUX) shows real-user experience. They're complementary — lab data catches regressions during development, field data validates real-world impact.

### Auto-timestamped output

**Why:** Running the tool repeatedly (e.g., via cron) should never overwrite previous results. Timestamped filenames (`20260216T143022Z-mobile.csv`) accumulate safely and enable historical comparison via the `compare` subcommand.

## System Architecture

### Data Flow

```
URL Input                API Layer              Processing            Output
─────────               ─────────              ──────────            ──────

positional args ─┐                                                   ┌─ CSV file
URL file (--file)─┼─ validate ─── URLs ──┐                           │
stdin (pipe)     ─┘                      │    ┌─ extract_metrics ─┐  ├─ JSON file
                                         ├────┤  (per response)  ├──┤
config file ──── load_config ── settings │    └──────────────────┘  ├─ HTML report
profile (--profile)── apply_profile ─────┘           │               │
                                         │    ThreadPoolExecutor     └─ Terminal table
                                         │    + Semaphore rate limit
                                         │           │
                                         └── fetch_pagespeed_result
                                              (with retry logic)
                                                     │
                                              PageSpeed API v5
```

### Component Responsibilities

| Component                                          | Responsibility                                                 |
| -------------------------------------------------- | -------------------------------------------------------------- |
| **CLI Parser** (`build_argument_parser`)           | Defines subcommands and flags, tracks explicit arguments       |
| **Config Loader** (`load_config`, `apply_profile`) | Reads TOML, merges settings/profiles/CLI with correct priority |
| **URL Handler** (`validate_url`, `load_urls`)      | Normalizes URLs, reads from args/file/stdin                    |
| **API Client** (`fetch_pagespeed_result`)          | HTTP requests with retry logic, error classification           |
| **Metrics Parser** (`extract_metrics`)             | Extracts lab + field data from API response into flat dict     |
| **Batch Processor** (`process_urls`)               | Orchestrates concurrent fetching with rate limiting            |
| **CSV Formatter** (`output_csv`)                   | Flat table output via pandas                                   |
| **JSON Formatter** (`output_json`)                 | Structured output with metadata envelope                       |
| **HTML Generator** (`generate_html_report`)        | Self-contained dashboard with inline CSS/JS                    |
| **Subcommand Handlers** (`cmd_*`)                  | Wire inputs to processing to outputs for each workflow         |

### Config Resolution Chain

```
CLI flags (explicit)
     │
     ▼
Profile values (--profile name)
     │
     ▼
[settings] from config file
     │
     ▼
Built-in defaults (hardcoded)
```

The `TrackingAction` argparse action records which flags were explicitly set on the command line. During config merging, only unset flags are filled from profile/settings/defaults.

## API Integration

### Endpoint

```
GET https://www.googleapis.com/pagespeedonline/v5/runPagespeed
```

### Parameters

| Parameter  | Value                                                                      |
| ---------- | -------------------------------------------------------------------------- |
| `url`      | Target URL to analyze                                                      |
| `strategy` | `mobile` or `desktop`                                                      |
| `category` | `performance`, `accessibility`, `best-practices`, `seo` (multiple allowed) |
| `key`      | API key (optional)                                                         |

### Response Structure

The API returns a large JSON object. The tool extracts from two main sections:

```
response
├── lighthouseResult
│   ├── categories.performance.score          → performance_score (×100)
│   ├── audits.first-contentful-paint         → lab_fcp_ms
│   ├── audits.largest-contentful-paint       → lab_lcp_ms
│   ├── audits.cumulative-layout-shift        → lab_cls
│   ├── audits.speed-index                    → lab_speed_index_ms
│   ├── audits.total-blocking-time            → lab_tbt_ms
│   └── audits.interactive                    → lab_tti_ms
│
└── loadingExperience
    └── metrics
        ├── FIRST_CONTENTFUL_PAINT_MS         → field_fcp_ms + category
        ├── LARGEST_CONTENTFUL_PAINT_MS       → field_lcp_ms + category
        ├── CUMULATIVE_LAYOUT_SHIFT_SCORE     → field_cls + category
        ├── INTERACTION_TO_NEXT_PAINT         → field_inp_ms + category
        ├── FIRST_INPUT_DELAY_MS              → field_fid_ms + category
        └── EXPERIMENTAL_TIME_TO_FIRST_BYTE   → field_ttfb_ms + category
```

### Rate Limiting Strategy

```
                          ┌──────────────┐
Worker 1 ───acquire───▶   │              │
Worker 2 ───acquire───▶   │  Semaphore   │ ──── only 1 request at a time
Worker 3 ───acquire───▶   │  (value=1)   │      despite N workers
Worker 4 ───acquire───▶   │              │
                          └──────┬───────┘
                                 │
                          delay between
                          requests (1.5s)
```

The semaphore ensures rate limiting even with multiple workers. Workers prepare results in parallel but serialize actual API calls.

### Retry Policy

| Status Code   | Action                                                         |
| ------------- | -------------------------------------------------------------- |
| 200           | Success — return response                                      |
| 429           | Rate limited — honor `Retry-After` header, exponential backoff |
| 500, 503      | Server error — exponential backoff (2s, 4s, 8s)                |
| 4xx (other)   | Permanent error — fail immediately                             |
| Network error | Retry with exponential backoff                                 |

Maximum 3 retries per request. After exhaustion, the URL is recorded with an error and processing continues.

## Concurrency Model

```
main thread
    │
    ├── ThreadPoolExecutor(max_workers=N)
    │       │
    │       ├── Worker 1 ──┐
    │       ├── Worker 2 ──┤
    │       ├── Worker 3 ──┼── Semaphore(1) ── rate-limited API call
    │       └── Worker 4 ──┘         │
    │                                │
    │                         delay enforcement
    │                         (time.monotonic)
    │
    ├── Lock for progress counter
    │
    └── Collect results via as_completed()
```

- **Workers** prepare and wait for their turn via the semaphore
- **Rate limiting** is enforced by checking elapsed time since last request
- **Progress** is reported thread-safely via a lock and counter
- **Error isolation**: one URL failing doesn't affect others — the error is recorded in the result dict

When `--workers 1` is specified, processing runs sequentially without ThreadPoolExecutor overhead.

## Output Formats

### CSV Schema

One row per (URL, strategy) pair. Column order:

```
url, strategy, performance_score, accessibility_score, best_practices_score, seo_score,
lab_fcp_ms, lab_lcp_ms, lab_cls, lab_speed_index_ms, lab_tbt_ms, lab_tti_ms,
field_fcp_ms, field_fcp_category, field_lcp_ms, field_lcp_category, ...
fetch_time, error
```

Missing values are empty cells (pandas default).

### JSON Schema

```json
{
  "metadata": {
    "generated_at": "ISO-8601 timestamp",
    "total_urls": "integer",
    "strategies": ["mobile", "desktop"],
    "tool_version": "semver"
  },
  "results": [
    {
      "url": "string",
      "strategy": "string",
      "performance_score": "integer|null",
      "lab_metrics": { "lab_fcp_ms": "number", ... },
      "field_metrics": { "field_lcp_ms": "number", "field_lcp_category": "string", ... },
      "fetch_time": "ISO-8601|null",
      "error": "string|null"
    }
  ]
}
```

Lab and field metrics are nested for readability. The metadata envelope enables automated processing of report files.

### HTML Report Architecture

The HTML report is self-contained — inline CSS and minimal inline JS, no external dependencies.

| Section          | Implementation                                           |
| ---------------- | -------------------------------------------------------- |
| Summary cards    | CSS Grid with score-colored values                       |
| Score table      | HTML table with color-coded cells via CSS classes        |
| Bar charts       | Pure CSS (`width: N%`) with colored backgrounds          |
| CWV indicators   | Pass/fail based on Google's published thresholds         |
| Sortable columns | Inline JS — `onclick` on `<th>` elements, DOM reordering |
| Field data table | Separate table, only rendered when field data exists     |

## Extensibility Points

### Adding new metrics

Add entries to `LAB_METRICS` or `FIELD_METRICS` lists:

```python
LAB_METRICS.append(("new-audit-id", "lab_new_metric_ms"))
FIELD_METRICS.append(("NEW_API_KEY", "field_new_ms", "field_new_category"))
```

Extraction happens automatically via the list-driven approach in `extract_metrics()`.

### Adding new output formats

1. Create an `output_newformat(dataframe, output_path)` function
2. Add the format to `VALID_OUTPUT_FORMATS`
3. Add a branch in `cmd_audit()` to call it

### Adding new subcommands

1. Add a subparser in `build_argument_parser()`
2. Create a `cmd_newcommand(args)` handler
3. Add to the dispatch dict in `main()`

### Custom profiles

Users can define any number of profiles in `pagespeed.toml` under `[profiles.name]`. Each profile can override any setting.

## Limitations and Future Considerations

### Current limitations

- **Single-file constraint**: All code is in one file (~800 lines). If the tool grows beyond ~1500 lines, consider splitting into a package with `src/` layout.
- **No persistent storage**: Reports are flat files. No database for querying historical trends.
- **FID deprecation**: First Input Delay is deprecated in favor of INP. The tool extracts both but FID may be removed from the API.
- **Field data availability**: CrUX data requires sufficient real-user traffic. Low-traffic sites will have empty field data columns.

### Potential future enhancements

- **CrUX API integration**: Dedicated CrUX API for field-only data without running Lighthouse
- **Trend analysis**: `trend` subcommand that loads multiple historical reports and charts score changes over time
- **Slack/email notifications**: Alert on regressions exceeding a threshold
- **GitHub Actions integration**: Run as a CI check on deployment
- **PDF export**: Generate PDF reports from the HTML template
