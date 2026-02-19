# Plan: Add --stream flag to audit subcommand

## Context

Users want to see PageSpeed results in real-time as they are fetched, and pipe them to downstream tools (e.g., `jq`). Currently, `cmd_audit` buffers all results, then writes files and prints a summary when the entire batch completes. This makes it impossible to act on individual results without waiting for the full run.

The `--stream` flag enables NDJSON output: one JSON object per line written to stdout as each URL/strategy result completes. File output is skipped when streaming. The progress bar (stderr) continues as-is.

## Decisions

- **Format**: NDJSON — one `json.dumps` line per result, to stdout via `out_console.print()`
- **File output**: Skipped when `--stream` is active (stream only)
- **Summary**: `_print_audit_summary()` skipped in stream mode (not useful when piping)
- **Budget**: Still evaluated if `--budget` is set (uses the complete DataFrame returned by `process_urls()`)
- **Multi-run (`--runs N`)**: Stream aggregated (median) result per URL/strategy after all N runs complete — not raw per-run rows

## File to Modify

`pagespeed_insights_tool.py` — all changes are in this single file.

---

## Implementation Steps

### Step 1 — Add `--stream` argparse flag

In the `audit` subparser block (near line 420, after the `--full` flag):

```python
audit_parser.add_argument(
    "--stream",
    dest="stream",
    action=TrackingStoreTrueAction,
    default=False,
    help="Print results as NDJSON to stdout as they complete (skips file output)",
)
```

### Step 2 — Add `on_result` callback parameter to `process_urls()`

Change signature (line 826):

```python
async def process_urls(
    urls: list[str],
    api_key: str | None,
    strategies: list[str],
    categories: list[str],
    delay: float,
    workers: int,
    verbose: bool = False,
    runs: int = 1,
    full: bool = False,
    on_result: Callable[[dict], None] | None = None,
) -> pd.DataFrame:
```

Add `Callable` to the imports from `collections.abc` (or `typing`). Check the existing import block first — if `Callable` isn't already imported, add it.

### Step 3 — Implement per-result streaming inside `process_single()`

Inside `process_urls()`, add a `run_accumulator` dict before the progress setup:

```python
run_accumulator: dict[tuple, list[dict]] = {}
if on_result and runs > 1:
    base_task_keys = [(url, strategy) for url, strategy in base_tasks]
    for key in base_task_keys:
        run_accumulator[key] = []
```

At the end of `process_single()` (after `progress.advance(prog_task)` and before `return metrics`):

```python
if on_result:
    if runs <= 1:
        on_result(metrics)
    else:
        key = (url, strategy)
        run_accumulator[key].append(metrics)
        if len(run_accumulator[key]) == runs:
            group_df = pd.DataFrame(run_accumulator[key])
            agg_df = aggregate_multi_run(group_df, runs)
            on_result(agg_df.iloc[0].to_dict())
```

### Step 4 — Add NDJSON serialization helper

Add a small helper function near the output utilities section (around line 1260):

```python
def _row_to_ndjson(row: dict) -> str:
    """Serialize a result dict to a single NDJSON line, replacing NaN with null."""
    cleaned = {}
    for key, value in row.items():
        try:
            cleaned[key] = None if pd.isna(value) else value
        except (TypeError, ValueError):
            cleaned[key] = value
    return json.dumps(cleaned, default=str)
```

### Step 5 — Wire up streaming in `cmd_audit()`

After reading `full = getattr(args, "full", False)` (around line 1551), add:

```python
stream = getattr(args, "stream", False)
on_result = None
if stream:
    def on_result(row: dict) -> None:
        out_console.print(_row_to_ndjson(row))
```

Pass `on_result` to `process_urls()`:

```python
dataframe = await process_urls(
    urls=urls,
    api_key=args.api_key,
    strategies=strategies,
    categories=categories,
    delay=args.delay,
    workers=args.workers,
    verbose=args.verbose,
    runs=runs,
    full=full,
    on_result=on_result,
)
```

Skip file output and summary when streaming:

```python
if not stream:
    _write_data_files(dataframe, output_format, output_dir, explicit_output, strategy_label)
    _print_audit_summary(dataframe)
```

Budget evaluation remains unconditional:

```python
if getattr(args, "budget", None):
    exit_code = await _apply_budget(dataframe, args)
    sys.exit(exit_code)
```

---

## Tests to Add

In `test_pagespeed_insights_tool.py`, in the existing `TestAuditParsing` class:

```python
def test_stream_flag_default_false(self):
    args = self.parser.parse_args(["audit"])
    self.assertFalse(args.stream)

def test_stream_flag_parses(self):
    args = self.parser.parse_args(["audit", "--stream"])
    self.assertTrue(args.stream)
```

In a new `TestAuditStream` class:

```python
class TestAuditStream(unittest.IsolatedAsyncioTestCase):
    async def test_stream_calls_on_result_per_url(self):
        """on_result callback is called once per URL/strategy in single-run mode."""
        mock_fetch = AsyncMock(return_value=FULL_API_RESPONSE)
        collected = []
        with patch("pagespeed_insights_tool.fetch_pagespeed_result", mock_fetch), \
             patch("pagespeed_insights_tool.time.monotonic", return_value=0.0):
            await pst.process_urls(
                urls=["https://a.com", "https://b.com"],
                api_key=None,
                strategies=["mobile"],
                categories=["performance"],
                delay=0,
                workers=1,
                on_result=collected.append,
            )
        self.assertEqual(len(collected), 2)
        self.assertEqual(collected[0]["url"], "https://a.com")
        self.assertEqual(collected[1]["url"], "https://b.com")

    async def test_stream_multi_run_emits_aggregated(self):
        """With runs=2, on_result is called once per URL/strategy (aggregated)."""
        mock_fetch = AsyncMock(return_value=FULL_API_RESPONSE)
        collected = []
        with patch("pagespeed_insights_tool.fetch_pagespeed_result", mock_fetch), \
             patch("pagespeed_insights_tool.time.monotonic", return_value=0.0):
            await pst.process_urls(
                urls=["https://a.com"],
                api_key=None,
                strategies=["mobile"],
                categories=["performance"],
                delay=0,
                workers=1,
                runs=2,
                on_result=collected.append,
            )
        self.assertEqual(len(collected), 1)
        self.assertIn("runs_completed", collected[0])
```

Also add a test for `cmd_audit` streaming mode that verifies `_write_data_files` is not called when `--stream` is set.

---

## Verification

```bash
# 1. Run the full test suite
uv run pytest test_pagespeed_insights_tool.py -v

# 2. Smoke-test streaming (requires a real or mocked API key)
pagespeed audit https://example.com --stream

# 3. Pipe to jq to verify valid NDJSON
pagespeed audit https://example.com https://google.com --stream | jq '.performance_score'

# 4. Verify file output is suppressed in stream mode
pagespeed audit https://example.com --stream  # no CSV/JSON files created

# 5. Confirm normal audit still works (no regression)
pagespeed audit https://example.com
```
