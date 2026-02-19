# Plan: Add `--full` Flag to `audit` Subcommand

## Context

The tool currently extracts a fixed set of metrics from the PageSpeed API's `lighthouseResult` field
and discards the rest. Power users may need the full Lighthouse audit data (all audits, opportunities,
diagnostics, metadata) for deeper analysis. The `--full` flag on `audit` preserves the complete
`lighthouseResult` object in the JSON output without changing the CSV or any other subcommand.

## Critical Files

- `pagespeed_insights_tool.py` — all changes
- `test_pagespeed_insights_tool.py` — new tests

## Implementation Steps

### 1. Add `--full` to `audit_parser` (argparse)

In `build_argument_parser()` (around line 381), add after `--categories`:

```python
audit_parser.add_argument(
    "--full",
    dest="full",
    action=TrackingStoreTrueAction,
    default=False,
    help="Include the raw lighthouseResult in JSON output (ignored for CSV)",
)
```

### 2. Extend `extract_metrics()` with `include_raw` parameter (line 757)

Add parameter `include_raw: bool = False`. At the end of the function, before `return row`:

```python
if include_raw:
    row["_lighthouse_raw"] = api_response.get("lighthouseResult")
```

`_lighthouse_raw` stores the complete `lighthouseResult` dict (already validated as present by
`fetch_pagespeed_result`).

### 3. Extend `process_urls()` with `full` parameter (line 816)

Add `full: bool = False` to the signature. In `process_single`, change the `extract_metrics` call:

```python
metrics = extract_metrics(response, url, strategy, include_raw=full)
```

### 4. Handle `_lighthouse_raw` in `aggregate_multi_run()` (line 895)

After the `fetch_time` block (line 951–952), add:

```python
if "_lighthouse_raw" in successful_runs.columns:
    non_null = successful_runs["_lighthouse_raw"].dropna()
    row["_lighthouse_raw"] = non_null.iloc[-1] if not non_null.empty else None
```

Takes the last run's raw lighthouse data (most recent), consistent with how `fetch_time` is handled.

### 5. Extend `output_json()` to emit `lighthouseResult` when present (line 1251)

In the per-result record-building loop, after `fetch_time`:

```python
if "_lighthouse_raw" in row.index and pd.notna(row["_lighthouse_raw"]):
    record["lighthouseResult"] = row["_lighthouse_raw"]
```

No change to the function signature — the output auto-includes the field when the column exists.

### 6. Protect `output_csv()` from the raw column (line 1235)

Add at the top of `output_csv()`, before `to_csv`:

```python
dataframe = dataframe.drop(columns=["_lighthouse_raw"], errors="ignore")
```

Ensures the raw dict column is never written to CSV regardless of how the function is called.

### 7. Wire `full` through `cmd_audit` (line 1518)

```python
full = getattr(args, "full", False)
```

Pass to `process_urls`:
```python
dataframe = await process_urls(..., full=full)
```

Modify the strategy label for file naming (auto-named files only; explicit `--output` paths are
left as provided by the user):
```python
strategy_label = args.strategy if args.strategy != "both" else "both"
if full:
    strategy_label = f"{strategy_label}-full"
```

Result: `20260219T143022Z-mobile-full.json`, `20260219T143022Z-both-full.csv`, etc.

## New Tests

Add to `TestExtractMetrics`:
- `test_include_raw_adds_lighthouse_raw` — verifies `_lighthouse_raw` equals the `lighthouseResult`
  dict from the fixture when `include_raw=True`
- `test_no_raw_by_default` — verifies `_lighthouse_raw` not in result when `include_raw=False`

Add to `TestOutputJson`:
- `test_full_includes_lighthouse_result` — verifies `results[0]["lighthouseResult"]` is present
  when `_lighthouse_raw` column exists in DataFrame
- `test_no_lighthouse_result_without_raw_column` — verifies `lighthouseResult` is absent when
  `_lighthouse_raw` column is absent

Add to `TestOutputCsv`:
- `test_drops_lighthouse_raw_column` — verifies `_lighthouse_raw` column is not present in the
  written CSV file

Add to `TestBuildArgumentParser`:
- `test_audit_full_flag_default_false` — verifies `--full` defaults to `False`
- `test_audit_full_flag_parses` — verifies `--full` sets `full=True`

Add to `TestAggregateMultiRun` (new class or existing):
- `test_aggregate_takes_last_lighthouse_raw` — verifies `_lighthouse_raw` from the last successful
  run is preserved after aggregation

## Verification

Run the full test suite:

```
uv run pytest test_pagespeed_insights_tool.py -v
```

Smoke test against a real URL (requires `PAGESPEED_API_KEY`):

```
pagespeed audit https://example.com --full --output-format json
# Confirm the output JSON contains a top-level "lighthouseResult" key per result
# Confirm the file is named like 20260219T143022Z-mobile-full.json

pagespeed audit https://example.com --full --output-format both
# Confirm CSV is written normally without _lighthouse_raw column
# Confirm JSON has lighthouseResult
```
