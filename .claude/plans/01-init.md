# Plan: PageSpeed Insights Batch Analysis CLI Tool

## Context

We need a command-line tool to automate PageSpeed Insights analysis across multiple URLs, replacing manual one-at-a-time checks. The tool queries Google's PageSpeed Insights API v5, extracts performance metrics (lab + field data), and outputs structured CSV/JSON reports. This is a greenfield project in an empty directory with `uv` 0.9.18 available.

## Approach: Single-File Script with PEP 723 Inline Metadata

Since the user wants a single `pagespeed_insights_tool.py` file managed by `uv`, we'll use PEP 723 inline script metadata (`# /// script` block). This lets `uv run pagespeed_insights_tool.py` automatically handle the virtual environment and dependencies — no `pyproject.toml` or project scaffolding needed.

## Files to Create

1. `pagespeed_insights_tool.py` — the CLI tool (single-file script)
2. `README.md` — project documentation with quickstart guide
3. `ARCHITECTURE.md` — technical architecture document

## Dependencies (inline PEP 723)

```python
# /// script
# requires-python = ">=3.13"
# dependencies = [
#   "requests",
#   "pandas",
# ]
# ///
```

## CLI Design: Subcommands + Config Profiles

The tool uses **argparse subcommands** for built-in task workflows, plus a **TOML config file** for custom/team-specific profiles.

### Subcommands

#### `quick-check` — Fast single-URL spot check

Prints a formatted table to stdout. No file output by default.

```bash
uv run pagespeed_insights_tool.py quick-check https://example.com
uv run pagespeed_insights_tool.py quick-check https://example.com --strategy both
```

Defaults: mobile only, performance category, table output to terminal.

#### `audit` — Full batch analysis (primary workflow)

Runs analysis on multiple URLs and writes report files.

```bash
uv run pagespeed_insights_tool.py audit -f urls.txt
uv run pagespeed_insights_tool.py audit -f urls.txt --strategy both --output-format both
uv run pagespeed_insights_tool.py audit https://a.com https://b.com -o report
```

Defaults: mobile, performance category, CSV output.

#### `compare` — Compare two reports and highlight regressions

Loads two previous CSV/JSON report files and shows differences.

```bash
uv run pagespeed_insights_tool.py compare before.csv after.csv
uv run pagespeed_insights_tool.py compare --threshold 5 old.json new.json
```

Outputs a table showing per-URL score changes, flagging regressions (score drops) and improvements. `--threshold` sets the minimum % change to highlight (default: 5).

#### `report` — Generate a visual HTML report from results

Takes a CSV or JSON output file and generates a self-contained HTML dashboard. Opens in any browser.

```bash
uv run pagespeed_insights_tool.py report results.csv
uv run pagespeed_insights_tool.py report results.json -o dashboard.html
uv run pagespeed_insights_tool.py report results.csv --open   # auto-open in browser
```

The HTML report includes:

- **Summary cards** — Total URLs, average score, best/worst performers
- **Color-coded score table** — Green (90-100), orange (50-89), red (0-49) for each URL
- **Core Web Vitals status** — Pass/fail indicators per metric using Google's thresholds (LCP < 2.5s, CLS < 0.1, INP < 200ms)
- **Bar charts** — Visual comparison of scores across URLs (pure CSS, no JS library needed)
- **Mobile vs Desktop comparison** — Side-by-side when both strategies are present
- **Sortable columns** — Minimal inline JS for clicking column headers to sort

The HTML is fully self-contained (inline CSS/JS, no external dependencies) so it can be shared as a single file.

#### `run` — Low-level direct access (all flags)

Full control with every CLI argument exposed. The "escape hatch" for anything the subcommands don't cover.

```bash
uv run pagespeed_insights_tool.py run https://example.com --strategy desktop --categories performance accessibility --delay 2.0
```

### Config File: `pagespeed.toml`

Optional config file for persistent settings and custom profiles. Looked up in the current directory, then `~/.config/pagespeed/config.toml`.

Every CLI option can be set in the config file. CLI flags always override config values.

```toml
[settings]
api_key = "YOUR_API_KEY"       # or use PAGESPEED_API_KEY env var
urls_file = "urls.txt"         # default URL file for -f flag (when no path given)
delay = 1.5                    # seconds between API requests
strategy = "mobile"            # default strategy: mobile, desktop, both
output_format = "csv"          # default output: csv, json, both
output_dir = "./reports"       # directory for output files (auto-created if missing)
workers = 4                    # concurrent workers (1 = sequential)
categories = ["performance"]   # default Lighthouse categories
verbose = false                # default verbosity

[profiles.quick]
strategy = "mobile"
output_format = "csv"
categories = ["performance"]

[profiles.full]
strategy = "both"
output_format = "both"
categories = ["performance", "accessibility", "best-practices", "seo"]

[profiles.core-vitals]
strategy = "both"
output_format = "csv"
categories = ["performance"]

[profiles.client-report]
urls_file = "client_urls.txt"
strategy = "both"
output_format = "both"
output_dir = "./client-reports"
categories = ["performance", "accessibility", "seo"]
```

**Config resolution order (highest priority wins):**

1. CLI flags (explicit arguments)
2. Profile values (via `--profile`)
3. `[settings]` defaults from config file
4. Built-in defaults (hardcoded in script)

Profiles are applied via `--profile` on `audit` or `run`:

```bash
uv run pagespeed_insights_tool.py audit -f urls.txt --profile full
uv run pagespeed_insights_tool.py run https://example.com --profile core-vitals
```

CLI flags override profile values when both are specified.

### Global Flags (available on all subcommands)

| Flag            | Short | Default                        | Description               |
| --------------- | ----- | ------------------------------ | ------------------------- |
| `--api-key`     | —     | config/env `PAGESPEED_API_KEY` | Google API key            |
| `--config`      | `-c`  | auto-discovered                | Path to config TOML file  |
| `--profile`     | `-p`  | `None`                         | Named profile from config |
| `-v, --verbose` | —     | `False`                        | Verbose output to stderr  |
| `--version`     | —     | —                              | Print version and exit    |

### `audit` / `run` Specific Flags

| Flag              | Short | Default           | Description                                       |
| ----------------- | ----- | ----------------- | ------------------------------------------------- |
| `urls`            | —     | `[]`              | Positional URLs                                   |
| `--file`          | `-f`  | `None`            | File with one URL per line                        |
| `--strategy`      | `-s`  | `mobile`          | `mobile`, `desktop`, or `both`                    |
| `--output-format` | —     | `csv`             | `csv`, `json`, or `both`                          |
| `--output`        | `-o`  | auto-timestamped  | Explicit output file path (overrides auto-naming) |
| `--output-dir`    | —     | `./reports/`      | Directory for auto-named output files             |
| `--delay`         | `-d`  | `1.5`             | Seconds between requests                          |
| `--workers`       | `-w`  | `4`               | Number of concurrent workers (1 = sequential)     |
| `--categories`    | —     | `['performance']` | Lighthouse categories                             |

**URL input priority:** positional args > `--file` > stdin (piped input via `sys.stdin.isatty()`)

## Metrics Extracted

### Lab Data (from `lighthouseResult.audits`)

| Metric                   | API Path                                               | Output Column        |
| ------------------------ | ------------------------------------------------------ | -------------------- |
| Performance Score        | `lighthouseResult.categories.performance.score` (x100) | `performance_score`  |
| First Contentful Paint   | `first-contentful-paint.numericValue`                  | `lab_fcp_ms`         |
| Largest Contentful Paint | `largest-contentful-paint.numericValue`                | `lab_lcp_ms`         |
| Cumulative Layout Shift  | `cumulative-layout-shift.numericValue`                 | `lab_cls`            |
| Speed Index              | `speed-index.numericValue`                             | `lab_speed_index_ms` |
| Total Blocking Time      | `total-blocking-time.numericValue`                     | `lab_tbt_ms`         |
| Time to Interactive      | `interactive.numericValue`                             | `lab_tti_ms`         |

### Field Data (from `loadingExperience.metrics` — may be absent for low-traffic sites)

| Metric           | API Key                           | Output Columns                         |
| ---------------- | --------------------------------- | -------------------------------------- |
| FCP              | `FIRST_CONTENTFUL_PAINT_MS`       | `field_fcp_ms`, `field_fcp_category`   |
| LCP              | `LARGEST_CONTENTFUL_PAINT_MS`     | `field_lcp_ms`, `field_lcp_category`   |
| CLS              | `CUMULATIVE_LAYOUT_SHIFT_SCORE`   | `field_cls`, `field_cls_category`      |
| INP              | `INTERACTION_TO_NEXT_PAINT`       | `field_inp_ms`, `field_inp_category`   |
| FID (deprecated) | `FIRST_INPUT_DELAY_MS`            | `field_fid_ms`, `field_fid_category`   |
| TTFB             | `EXPERIMENTAL_TIME_TO_FIRST_BYTE` | `field_ttfb_ms`, `field_ttfb_category` |

## Function Architecture

```
main()
├── build_argument_parser() → argparse.ArgumentParser (with subparsers)
├── load_config(config_path) → dict                   # parse pagespeed.toml
├── apply_profile(args, config, profile_name) → args   # merge profile into args
│
├── cmd_quick_check(args)                              # quick-check subcommand
│   ├── fetch_pagespeed_result() + extract_metrics()
│   └── print_table_to_stdout()
│
├── cmd_audit(args)                                    # audit subcommand
│   ├── load_urls(url_args, file_path, stdin) → list[str]
│   │   └── validate_url(url) → str
│   ├── process_urls(urls, api_key, strategies, delay, workers) → pd.DataFrame
│   │   ├── ThreadPoolExecutor(max_workers=workers)
│   │   ├── threading.Semaphore for rate limiting
│   │   ├── fetch_pagespeed_result(url, api_key, strategy) → dict
│   │   │   └── Retry logic: exponential backoff on 429/500/503
│   │   └── extract_metrics(api_response, url, strategy) → dict
│   ├── output_csv(dataframe, output_path) → str
│   └── output_json(dataframe, output_path) → str
│
├── cmd_compare(args)                                  # compare subcommand
│   ├── load_report(file_path) → pd.DataFrame
│   └── print_comparison_table()
│
├── cmd_report(args)                                   # report subcommand
│   ├── load_report(file_path) → pd.DataFrame
│   ├── generate_html_report(dataframe) → str          # builds self-contained HTML
│   └── optionally open in browser (webbrowser.open)
│
└── cmd_run(args)                                      # run subcommand (same as audit internals)
```

## Error Handling Strategy

1. **Input validation**: Invalid URLs logged to stderr and skipped; missing file exits with code 1
2. **API retries**: Exponential backoff (2s, 4s, 8s) on 429/500/503 errors, up to 3 retries; `Retry-After` header honored on 429
3. **Per-URL containment**: Failed URLs get an `error` column value; processing continues with remaining URLs
4. **Rate limiting**: Shared `threading.Semaphore` + minimum delay between requests ensures we stay within API limits even with concurrent workers
5. **Config errors**: Missing config file is silently ignored (it's optional); malformed TOML exits with a clear error message

## Output Formats

### Default File Naming (cron-friendly)

Output files use UTC timestamps by default — no manual naming needed, safe for repeated cron runs:

```
{output_dir}/{YYYYMMDD}T{HHMMSS}Z-{strategy}.{ext}
```

Examples:

```
./reports/20260216T143022Z-mobile.csv
./reports/20260216T150000Z-both.json
./reports/20260216T143022Z-mobile.html
```

- `output_dir` defaults to `./reports/` (auto-created if missing)
- Configurable via `--output-dir`, config `output_dir`, or profile `output_dir`
- When `-o` is specified, it's used as-is (overrides the auto-naming)
- The `report` subcommand follows the same pattern: `{YYYYMMDD}T{HHMMSS}Z-report.html`

**Cron example** — no arguments needed beyond the URL source:

```bash
# Every Monday at 6am UTC — outputs accumulate in ./reports/ with unique timestamps
0 6 * * 1 cd /path/to/project && uv run pagespeed_insights_tool.py audit -f urls.txt --profile full
```

### CSV

Flat table, one row per (url, strategy) pair. Missing values as empty cells.

### JSON

Structured with metadata header:

```json
{
  "metadata": { "generated_at": "...", "total_urls": 5, "strategies": [...] },
  "results": [{ "url": "...", "strategy": "...", "performance_score": 92, "lab_metrics": {...}, "field_metrics": {...}, "error": null }]
}
```

### Terminal table

`quick-check` only — formatted with column alignment, printed to stdout.

## README.md Contents

The README will include these sections:

### Structure

1. **Title + one-line description**
2. **Quickstart** — 3 steps to first result:
   - Install uv (link to astral.sh)
   - Run `uv run pagespeed_insights_tool.py quick-check https://example.com`
   - (Optional) Set API key: `export PAGESPEED_API_KEY=your_key`
3. **Prerequisites** — Python 3.13+, uv, optional Google API key
4. **Getting an API Key** — Step-by-step for Google Cloud Console
5. **Usage** — All 4 subcommands with examples:
   - `quick-check` with sample terminal output
   - `audit` with file input and output examples
   - `compare` with regression example
   - `run` for advanced usage
6. **Configuration** — `pagespeed.toml` format, profile examples, config file discovery
7. **Output Formats** — CSV column descriptions, JSON schema
8. **Metrics Reference** — Table of all metrics with descriptions and good/needs-work/poor thresholds
9. **Rate Limits** — What to expect with/without API key, `--delay` tuning

## ARCHITECTURE.md Contents

The architecture document will cover:

1. **Project Overview** — Problem statement, goals, and scope of the tool
2. **Design Decisions**
   - Why single-file PEP 723 script (vs full package): simplicity, zero-setup, `uv run` portability
   - Why argparse over click/typer: no extra dependencies, stdlib is sufficient
   - Why ThreadPoolExecutor over asyncio: simpler mental model, adequate for I/O-bound API calls
   - Why TOML for config (vs YAML/JSON): Python 3.11+ has `tomllib` in stdlib, human-readable
   - Why both lab and field data: complementary views of performance
   - Why auto-timestamped output: cron-safe, no overwrites, historical tracking
3. **System Architecture**
   - Data flow diagram: URL input → validation → API calls (parallel) → parsing → DataFrame → output
   - Component responsibilities (input layer, API client, parser, output formatters, CLI dispatcher)
   - Config resolution chain (CLI > profile > settings > defaults)
4. **API Integration**
   - PageSpeed Insights API v5 endpoint and parameters
   - Response structure overview (lab vs field data paths)
   - Rate limiting strategy (semaphore + delay + exponential backoff)
   - Retry policy and error classification (retryable vs permanent)
5. **Concurrency Model**
   - ThreadPoolExecutor with shared semaphore for rate limiting
   - Thread-safe progress reporting
   - Error isolation per worker (one URL failure doesn't affect others)
6. **Output Formats**
   - CSV schema and column ordering rationale
   - JSON schema with metadata envelope
   - HTML report architecture (self-contained, inline CSS/JS)
7. **Extensibility Points**
   - Adding new metrics (constants mapping)
   - Adding new output formats
   - Adding new subcommands
   - Custom profiles via TOML
8. **Limitations and Future Considerations**
   - Single-file constraint (when to consider a package structure)
   - No persistent storage / database (reports are file-based)
   - FID deprecation timeline
   - Potential CrUX API integration for dedicated field data

## Implementation Order

1. Script header (PEP 723 metadata, constants, `PageSpeedError` exception)
2. `load_config()` + `apply_profile()` — config/profile handling
3. `build_argument_parser()` — subcommand CLI with all flags
4. `validate_url()` + `load_urls()` — input handling
5. `fetch_pagespeed_result()` — core API client with retry logic
6. `extract_metrics()` — response parsing
7. `process_urls()` — batch orchestration with progress output
8. `output_csv()` + `output_json()` — serialization
9. `cmd_quick_check()` — quick-check subcommand with terminal table
10. `cmd_audit()` — audit subcommand wiring
11. `cmd_compare()` — report comparison logic
12. `cmd_report()` + `generate_html_report()` — HTML dashboard generation
13. `cmd_run()` — low-level run subcommand
14. `main()` — dispatch to subcommands
15. `README.md` — quickstart guide, usage docs, metrics reference
16. `ARCHITECTURE.md` — scope, design decisions, system architecture, extensibility

## Verification

```bash
# Help and subcommand discovery
uv run pagespeed_insights_tool.py --help
uv run pagespeed_insights_tool.py quick-check --help

# Quick spot check
uv run pagespeed_insights_tool.py quick-check https://www.google.com

# Batch audit
echo "https://www.google.com" > urls.txt
uv run pagespeed_insights_tool.py audit -f urls.txt --strategy both --output-format both -o test_output

# Compare two reports
uv run pagespeed_insights_tool.py compare test_output.csv test_output2.csv

# Generate visual HTML report
uv run pagespeed_insights_tool.py report test_output.csv --open

# Low-level run
uv run pagespeed_insights_tool.py run https://www.google.com --strategy desktop --verbose

# Profile usage (after creating pagespeed.toml)
uv run pagespeed_insights_tool.py audit -f urls.txt --profile full
```
