# PageSpeed Insights Batch Analysis Tool

A command-line tool that automates Google PageSpeed Insights analysis across multiple URLs, extracting performance metrics (lab + field data) into structured CSV, JSON, and HTML reports.

## Installation

### Run instantly with `uvx` (recommended, no install needed)

```bash
uvx pagespeed-insights quick-check https://example.com
```

### Install with `pip` or `pipx`

```bash
pip install pagespeed-insights
pagespeed quick-check https://example.com
```

### Run from URL (just needs `uv`)

```bash
uv run https://raw.githubusercontent.com/volkanunsal/pagespeed/main/pagespeed_insights_tool.py quick-check https://example.com
```

### Development

```bash
git clone https://github.com/volkanunsal/pagespeed.git
cd pagespeed
uv run pagespeed_insights_tool.py quick-check https://example.com
```

## Prerequisites

- **Python 3.13+**
- **Google API key** (optional) — without one, you're limited to ~25 queries/day; with one, ~25,000/day

## Getting an API Key

1. Go to the [Google Cloud Console](https://console.cloud.google.com/)
2. Create a new project (or select an existing one)
3. Navigate to **APIs & Services > Library**
4. Search for **PageSpeed Insights API** and enable it
5. Go to **APIs & Services > Credentials**
6. Click **Create Credentials > API Key**
7. Copy the key and set it:
   ```bash
   export PAGESPEED_API_KEY=your_key_here
   ```
   Or add it to your `pagespeed.toml` config file (see [Configuration](#configuration)).

## Usage

### `quick-check` — Fast single-URL spot check

Prints a formatted report to the terminal. No files written.

```bash
# Mobile only (default)
pagespeed quick-check https://www.google.com

# Both mobile and desktop
pagespeed quick-check https://www.google.com --strategy both

# With specific categories
pagespeed quick-check https://www.google.com --categories performance accessibility
```

Sample output:

```
============================================================
  URL:      https://www.google.com
  Strategy: mobile
============================================================
  Performance Score: 92/100 (GOOD)

  --- Lab Data ---
  First Contentful Paint............. 1200ms
  Largest Contentful Paint........... 1800ms
  Cumulative Layout Shift............ 0.0100
  Speed Index........................ 1500ms
  Total Blocking Time................ 150ms
  Time to Interactive................ 2100ms
```

### `audit` — Full batch analysis

Analyzes multiple URLs and writes CSV/JSON reports.

```bash
# From a file of URLs
pagespeed audit -f urls.txt

# Multiple strategies and output formats
pagespeed audit -f urls.txt --strategy both --output-format both

# Inline URLs with custom output path
pagespeed audit https://a.com https://b.com -o report

# With a named profile
pagespeed audit -f urls.txt --profile full

# Piped input
cat urls.txt | pagespeed audit
```

The URL file is one URL per line. Lines starting with `#` are comments:

```
# Main pages
https://example.com
https://example.com/about
https://example.com/contact
```

### `compare` — Compare two reports

Loads two previous report files and shows per-URL score changes.

```bash
# Compare before and after
pagespeed compare before.csv after.csv

# Custom threshold (flag changes >= 10%)
pagespeed compare --threshold 10 old.json new.json
```

Output flags regressions with `!!` and improvements with `++`.

### `report` — Generate HTML dashboard

Creates a self-contained HTML report from a results file.

```bash
# Generate HTML from CSV results
pagespeed report results.csv

# Custom output path
pagespeed report results.json -o dashboard.html

# Auto-open in browser
pagespeed report results.csv --open
```

The HTML report includes:
- Summary cards (total URLs, average/best/worst scores)
- Color-coded score table (green/orange/red)
- Core Web Vitals pass/fail indicators
- Bar charts comparing scores across URLs
- Field data table (when available)
- Sortable columns (click headers)

### `run` — Low-level direct access

Full control with every CLI flag. Same internals as `audit`.

```bash
pagespeed run https://example.com --strategy desktop --categories performance accessibility --delay 2.0
```

## Configuration

### Config file: `pagespeed.toml`

An optional TOML file for persistent settings and named profiles. The tool searches for it in:
1. Current working directory (`./pagespeed.toml`)
2. User config directory (`~/.config/pagespeed/config.toml`)

You can also pass an explicit path with `--config path/to/config.toml`.

```toml
[settings]
api_key = "YOUR_API_KEY"       # or use PAGESPEED_API_KEY env var
urls_file = "urls.txt"         # default URL file for -f
delay = 1.5                    # seconds between API requests
strategy = "mobile"            # mobile, desktop, or both
output_format = "csv"          # csv, json, or both
output_dir = "./reports"       # directory for output files
workers = 4                    # concurrent workers (1 = sequential)
categories = ["performance"]   # Lighthouse categories
verbose = false

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

### Config resolution order

Settings are merged with the following priority (highest wins):

1. **CLI flags** — explicit command-line arguments
2. **Profile values** — via `--profile name`
3. **`[settings]`** — defaults from config file
4. **Built-in defaults** — hardcoded in the script

### Global flags

| Flag | Short | Default | Description |
|------|-------|---------|-------------|
| `--api-key` | — | config/env | Google API key |
| `--config` | `-c` | auto-discovered | Path to config TOML |
| `--profile` | `-p` | None | Named profile from config |
| `--verbose` | `-v` | False | Verbose output to stderr |
| `--version` | — | — | Print version and exit |

### `audit` / `run` flags

| Flag | Short | Default | Description |
|------|-------|---------|-------------|
| `urls` | — | `[]` | Positional URLs |
| `--file` | `-f` | None | File with one URL per line |
| `--strategy` | `-s` | `mobile` | `mobile`, `desktop`, or `both` |
| `--output-format` | — | `csv` | `csv`, `json`, or `both` |
| `--output` | `-o` | auto-timestamped | Explicit output file path |
| `--output-dir` | — | `./reports/` | Directory for auto-named files |
| `--delay` | `-d` | `1.5` | Seconds between requests |
| `--workers` | `-w` | `4` | Concurrent workers |
| `--categories` | — | `performance` | Lighthouse categories |

## Output Formats

### File naming

By default, output files use UTC timestamps:

```
{output_dir}/{YYYYMMDD}T{HHMMSS}Z-{strategy}.{ext}
```

Examples:
```
./reports/20260216T143022Z-mobile.csv
./reports/20260216T150000Z-both.json
./reports/20260216T143022Z-report.html
```

Use `-o` to override with an explicit path.

### CSV

Flat table with one row per (URL, strategy) pair. Columns:

| Column | Description |
|--------|-------------|
| `url` | The analyzed URL |
| `strategy` | `mobile` or `desktop` |
| `performance_score` | 0-100 Lighthouse score |
| `lab_fcp_ms` | First Contentful Paint (ms) |
| `lab_lcp_ms` | Largest Contentful Paint (ms) |
| `lab_cls` | Cumulative Layout Shift |
| `lab_speed_index_ms` | Speed Index (ms) |
| `lab_tbt_ms` | Total Blocking Time (ms) |
| `lab_tti_ms` | Time to Interactive (ms) |
| `field_*` | Field (CrUX) metrics (when available) |
| `error` | Error message if the request failed |

### JSON

Structured with metadata header:

```json
{
  "metadata": {
    "generated_at": "2026-02-16T14:30:22+00:00",
    "total_urls": 5,
    "strategies": ["mobile", "desktop"],
    "tool_version": "1.0.0"
  },
  "results": [
    {
      "url": "https://example.com",
      "strategy": "mobile",
      "performance_score": 92,
      "lab_metrics": { "lab_fcp_ms": 1200, "lab_lcp_ms": 1800, ... },
      "field_metrics": { "field_lcp_ms": 2100, "field_lcp_category": "FAST", ... },
      "error": null
    }
  ]
}
```

## Metrics Reference

### Lab data (synthetic, from Lighthouse)

| Metric | Good | Needs Work | Poor |
|--------|------|-----------|------|
| First Contentful Paint | < 1.8s | 1.8s–3.0s | > 3.0s |
| Largest Contentful Paint | < 2.5s | 2.5s–4.0s | > 4.0s |
| Cumulative Layout Shift | < 0.1 | 0.1–0.25 | > 0.25 |
| Total Blocking Time | < 200ms | 200ms–600ms | > 600ms |
| Speed Index | < 3.4s | 3.4s–5.8s | > 5.8s |
| Time to Interactive | < 3.8s | 3.8s–7.3s | > 7.3s |

### Field data (real users, from CrUX)

Field data comes from the Chrome User Experience Report. It may not be available for low-traffic sites.

| Metric | Description |
|--------|-------------|
| FCP | First Contentful Paint — when first content appears |
| LCP | Largest Contentful Paint — when main content loads |
| CLS | Cumulative Layout Shift — visual stability |
| INP | Interaction to Next Paint — input responsiveness |
| FID | First Input Delay — (deprecated, replaced by INP) |
| TTFB | Time to First Byte — server response time |

## Rate Limits

| Scenario | Limit |
|----------|-------|
| Without API key | ~25 queries/100 seconds |
| With API key | ~25,000 queries/day (400/100 seconds) |

Tips:
- Use `--delay` to increase time between requests if hitting rate limits
- Use `--workers 1` for sequential processing (safest for rate limits)
- The tool retries on 429 (rate limit) responses with exponential backoff

## Cron usage

Output files auto-increment with timestamps, so cron jobs won't overwrite previous results:

```bash
# Every Monday at 6am UTC
0 6 * * 1 cd /path/to/project && pagespeed audit -f urls.txt --profile full
```

## Testing

The project includes a comprehensive test suite (102 tests across 18 test classes). All tests run offline — API calls, sitemap fetches, and file I/O are mocked.

```bash
# Run all tests
uv run test_pagespeed_insights_tool.py -v

# Run a single test class
uv run test_pagespeed_insights_tool.py -v TestValidateUrl

# Run a specific test method
uv run test_pagespeed_insights_tool.py -v TestExtractMetrics.test_full_extraction
```

## License

This project is licensed under the [MIT License](LICENSE).
