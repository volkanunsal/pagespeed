# Plan: Multi-Run Median Scoring

## Context

Lab scores from Lighthouse can vary 5-10 points between runs due to network conditions, server load, and other transient factors. A single run may produce outlier results that misrepresent actual performance. This feature mitigates that variability by running each (URL, strategy) analysis N times and computing the median across all numeric metrics, producing more reliable and reproducible scores.

## Usage Examples

```bash
# Quick spot check with 3 runs for stability
pagespeed quick-check https://example.com --runs 3

# Full audit with median of 5 runs
pagespeed audit -f urls.txt --strategy both --runs 5

# Pipeline with budget enforcement on median values
pagespeed pipeline https://example.com --runs 3 --budget cwv

# Config file: runs = 3 in [settings] or [profiles.stable]
```

## Design Decisions

1. **Aggregation as a separate function** — `aggregate_multi_run()` sits between `process_urls()` (which collects raw results) and downstream consumers. This keeps `process_urls()` focused on concurrency/rate-limiting.

2. **Interleaved run ordering** — Task list runs all URLs once, then all URLs again, etc. This spreads load over time per server and avoids cache-warming bias from sequential same-URL runs.

3. **Field metrics pass through unchanged** — CrUX data is a 28-day window that doesn't vary between runs. Median of identical values is harmless, no special handling needed.

4. **Metadata columns** — When `runs > 1`, three columns are added: `runs_completed`, `score_range`, `score_stddev`. These signal confidence to the user and appear in all output formats.

5. **Default `--runs 1`** — Zero behavior change. The `aggregate_multi_run()` fast-path returns the DataFrame unchanged, no metadata columns added.

## Implementation Steps

### Step 1: Constants

Add after existing defaults (~line 51):

- `DEFAULT_RUNS = 1`
- `MEDIAN_ELIGIBLE_COLUMNS` set — all numeric columns from scores, `LAB_METRICS`, and `FIELD_METRICS` value columns (not `*_category`). Explicitly enumerated for safety.

### Step 2: CLI flag `--runs` / `-n`

Add to **four** subparsers: `quick-check`, `audit`, `run`, `pipeline`. Uses `TrackingAction` like other flags:

```python
parser.add_argument("-n", "--runs", dest="runs", action=TrackingAction,
                    type=int, default=DEFAULT_RUNS,
                    help="Number of analysis runs per URL for median scoring (default: 1)")
```

Add `"runs": "runs"` to `config_key_map` in `apply_profile()` for TOML config support.

### Step 3: `aggregate_multi_run(dataframe, total_runs)` function

New function after `process_urls()`. Algorithm:

1. **Fast path:** `total_runs <= 1` → return unchanged.
2. **Group by** `(url, strategy)`.
3. **Per group:** separate successful rows (error is null) from failures.
   - All failed → single error row, `runs_completed=0`.
   - Has successes → compute:
     - **Numeric columns** (`MEDIAN_ELIGIBLE_COLUMNS`): `pandas.Series.median()`, round to match original precision (scores → int, `*_cls` → 4 decimals, `*_ms` → int).
     - **Category columns** (`*_category`): mode (most frequent value).
     - **`fetch_time`**: last value.
     - **`error`**: `None`.
   - Add metadata: `runs_completed`, `score_range` (max - min of `performance_score`), `score_stddev`.
4. Reassemble into DataFrame with one row per (url, strategy).

### Step 4: Modify `process_urls()`

- Add `runs: int = 1` parameter.
- Build interleaved task list: `base_tasks * runs` where `base_tasks = [(url, strategy) for url in urls for strategy in strategies]`.
- Update progress reporting to show run context when `runs > 1`.
- After collecting raw results into DataFrame, call `aggregate_multi_run(raw_df, runs)`.
- Return the aggregated DataFrame (one row per url/strategy, same as today).

### Step 5: Update callers

- **`cmd_audit()`**: Pass `runs=args.runs` to `process_urls()`. Add run count to progress message.
- **`cmd_pipeline()`**: Same.
- **`cmd_run()`**: Delegates to `cmd_audit()`, no separate change.
- **`cmd_quick_check()`**: Does not use `process_urls()`. Loop `fetch_pagespeed_result()` N times per strategy, collect metrics, aggregate via `aggregate_multi_run()` when `runs > 1`. Pass metadata flag to `format_terminal_table()`.

### Step 6: Output format updates

- **`format_terminal_table()`**: Add `show_run_metadata` param. When true and `runs_completed > 1`, show "Median of N runs, range: X, stddev: Y" after the performance score.
- **`_print_audit_summary()`**: When `runs_completed` column exists, print "Runs/URL: N (median scoring)".
- **`output_json()`**: Add `runs_per_url` and `aggregation: "median"` to metadata envelope when multi-run.
- **`generate_html_report()`**: Add "Runs/URL" summary card when multi-run.

### Step 7: Input validation

In `cmd_audit()`, `cmd_pipeline()`, `cmd_quick_check()`:
- `runs < 1` → error exit.
- `runs > 10` → print warning about API usage (not an error).

## Files to Modify

- `pagespeed_insights_tool.py` — all changes (constants, parser, new function, callers, formatters)
- `CLAUDE.md` — add `--runs` to Running the Tool examples and audit/run flags table

## Edge Cases

| Case | Behavior |
|------|----------|
| `--runs 1` (default) | No aggregation, no metadata columns, identical to today |
| 2 of 3 runs fail | Median from 2 successes, `runs_completed=2` |
| All runs fail | Single error row, `runs_completed=0` |
| Even run count | pandas median averages the two middle values |
| Budget eval | Works on aggregated DataFrame — evaluates median values |
| Compare command | Operates on saved files, unaffected |

## Verification

```bash
# Help text
pagespeed quick-check --help | grep runs

# Default behavior unchanged
pagespeed quick-check https://example.com

# Multi-run quick-check shows median + range
pagespeed quick-check https://example.com --runs 3

# Multi-run audit CSV has runs_completed column
pagespeed audit https://example.com --runs 3 -o test-run

# Budget works on median values
pagespeed pipeline https://example.com --runs 3 --budget cwv

# Run existing test suite (no regressions)
uv run test_pagespeed_insights_tool.py -v
```
