# Plan 12: Write permanently failing URLs to errors.csv

## Context

When the PageSpeed API returns a non-retryable error for a URL (e.g. `HTTP 400: FAILED_DOCUMENT_REQUEST`), the row is recorded in the main DataFrame with an `error` column set and all metric columns as `NaN`. These error rows are currently mixed into the same output file as successful rows. The user wants error rows separated into a dedicated `errors.csv` file for easier triage.

## Approach

Modify `_write_data_files()` to detect error rows in the DataFrame and, if any exist, write them to a companion `errors.csv` file in the same output directory.

- **No new functions needed** — reuse `output_csv()` and `generate_output_path()`.
- **Only one code location** — `_write_data_files()` is the single place where file output happens (called by `cmd_audit`, `cmd_pipeline`, `cmd_run`). `cmd_quick_check` is terminal-only and produces no files, so it's intentionally excluded.
- **Error rows are identified** by `dataframe["error"].notna()` — the same pattern used everywhere else in the codebase.

## Files to Modify

- `pagespeed_insights_tool.py` — `_write_data_files()` function (~line 975)

## Implementation

In `_write_data_files()`, after writing the main data files, add:

```python
# Write errors.csv if there are any failed URLs
if "error" in dataframe.columns:
    error_rows = dataframe[dataframe["error"].notna()]
    if not error_rows.empty:
        errors_path = generate_output_path(output_dir, "errors", "csv")
        output_csv(error_rows[["url", "strategy", "error"]], errors_path)
        err_console.print(f"  [yellow]⚠[/yellow]  [cyan]{errors_path}[/cyan] ({len(error_rows)} failed URL{'s' if len(error_rows) != 1 else ''})")
```

Key decisions:
- **Filename**: `{timestamp}-errors.csv` — follows the existing auto-timestamped pattern via `generate_output_path(output_dir, "errors", "csv")`
- **Columns**: only `url`, `strategy`, `error` — the three columns always present on error rows; metric columns are all `NaN` and add no value
- **Always CSV**: errors file is always CSV regardless of `output_format` (it's a diagnostic artifact, not a data report)
- **Conditional**: only written when errors exist — no empty file created when all URLs succeed

## Worktree

Branch/dir: `012-errors-csv`

## Verification

Run the test suite:
```
uv run pytest test_pagespeed_insights_tool.py -v
```
