# Plan: Remove Multi-Run Feature

## Context

The multi-run (`--runs N`) feature runs each URL N times and aggregates results using median scoring. It adds significant complexity (a separate aggregation function, extra metadata columns, interleaved task scheduling, loop logic in `cmd_quick_check`) for a use case the project is moving away from. The goal is to simplify the tool to a single-analysis-at-a-time model across all subcommands.

## File to Modify

- `pagespeed_insights_tool.py` — all logic changes
- `test_pagespeed_insights_tool.py` — remove multi-run tests
- `README.md` — remove multi-run documentation

---

## Implementation Steps

### Step 1 — Remove constants

- Delete `DEFAULT_RUNS = 1` (line 70)
- Delete the entire `MEDIAN_ELIGIBLE_COLUMNS` set (lines 139–150) — only used by `aggregate_multi_run`

### Step 2 — Remove config key mapping

In `apply_profile()`, delete the `"runs": "runs"` entry from `config_key_map` (line 295).

### Step 3 — Remove `--runs` from all four subparsers

Delete the `add_argument("-n", "--runs", ...)` line from:
- `quick_check_parser` (line 367)
- `audit_parser` (line 387)
- `run_parser` (line 434)
- `pipeline_parser` (line 456)

### Step 4 — Simplify `process_urls()`

**Signature** — remove `runs: int = 1` parameter.

**Body** — replace the current interleaved-run setup with a simpler form:

```python
# Before
base_tasks = [(url, strategy) for url in urls for strategy in strategies]
task_list = base_tasks * runs
total_tasks = len(task_list)
base_count = len(base_tasks)
...
run_accumulator: dict[tuple, list[dict]] = {}
if on_result and runs > 1:
    for key in base_tasks:
        run_accumulator[key] = []

# After
task_list = [(url, strategy) for url in urls for strategy in strategies]
total_tasks = len(task_list)
```

Inside `process_single()`:
- Remove `run_label` and `v_run_label` ternaries — use static descriptions:
  ```python
  progress.update(prog_task, description=f"[cyan]{short_url}[/cyan] ({strategy})")
  # verbose:
  err_console.print(f"  [dim]Fetching[/dim] [cyan]{url}[/cyan] ({strategy})...")
  ```
- Replace the multi-branch `on_result` block with:
  ```python
  if on_result:
      on_result(metrics)
  ```

**Return** — replace `return aggregate_multi_run(raw_dataframe, runs)` with `return pd.DataFrame(results)`.

### Step 5 — Delete `aggregate_multi_run()`

Remove the entire function (lines 933–1012).

### Step 6 — Simplify `cmd_quick_check()`

Replace the runs-loop structure with a single fetch per strategy:

```python
async def cmd_quick_check(args: argparse.Namespace) -> None:
    url = validate_url(args.url)
    if not url:
        err_console.print(f"[bold red]Error:[/bold red] invalid URL: {args.url}")
        sys.exit(1)

    strategies = [args.strategy] if args.strategy != "both" else ["mobile", "desktop"]
    categories = getattr(args, "categories", DEFAULT_CATEGORIES)

    results = []
    async with httpx.AsyncClient() as client:
        for strategy in strategies:
            with err_console.status(f"Fetching [cyan]{url}[/cyan] ({strategy})...", spinner="dots"):
                try:
                    response = await fetch_pagespeed_result(url, strategy, args.api_key, categories, client=client)
                    results.append(extract_metrics(response, url, strategy))
                except PageSpeedError as exc:
                    results.append({"url": url, "strategy": strategy, "error": str(exc)})

    out_console.print(format_terminal_table(results))
```

### Step 7 — Simplify `cmd_audit()`

Remove:
- `runs = getattr(args, "runs", 1)`
- `if runs < 1:` validation block
- `runs_label` and its use in the `err_console.print(...)` call
- `runs=runs,` from the `process_urls()` call

### Step 8 — Simplify `cmd_pipeline()`

Same removals as Step 7 (lines 2106–2124).

### Step 9 — Clean up `format_terminal_table()`

- Remove the `show_run_metadata: bool = False` parameter
- Remove the "Run metadata" block (lines 1416–1426) that displays `runs_completed`, `score_range`, `score_stddev`

### Step 10 — Clean up `_print_audit_summary()`

Remove the `runs_completed` block (lines 1087–1090):
```python
# Remove:
if "runs_completed" in dataframe.columns:
    max_runs = dataframe["runs_completed"].max()
    if max_runs > 1:
        t.add_row("Runs/URL", Text(f"{max_runs} (median scoring)", style="dim"))
```

### Step 11 — Clean up `output_json()`

Remove per-row multi-run metadata (in the row-building loop):
```python
# Remove:
for meta_key in ("runs_completed", "score_range", "score_stddev"):
    if meta_key in row and pd.notna(row[meta_key]):
        record[meta_key] = row[meta_key]
```

Remove the top-level runs metadata block at the end:
```python
# Remove:
if "runs_completed" in dataframe.columns and len(dataframe) > 0:
    max_runs = int(dataframe["runs_completed"].max())
    if max_runs > 1:
        output_data["metadata"]["runs_per_url"] = max_runs
        output_data["metadata"]["aggregation"] = "median"
```

### Step 12 — Clean up `generate_html_report()`

Remove the `runs_card` variable definition (~line 1749) and its `{runs_card}` interpolation in the HTML template (~line 1973).

---

## Test Changes (`test_pagespeed_insights_tool.py`)

1. **Remove** `TestAggregateMultiRun` class entirely.
2. **Remove** `test_stream_multi_run_emits_aggregated` from `TestAuditStream`.
3. **Remove** `assertIn("runs_completed", ...)` assertion from any remaining streaming test.
4. Check `TestProcessUrls` for any `runs=` kwargs and remove them.

---

## README Changes

1. Remove `pagespeed audit -f urls.txt --runs 3 --stream ...` example from the `--stream` usage block.
2. Remove the "Multi-run (`--runs N`)" bullet from the `#### --stream flag` section.
3. Remove "Multi-run: when used with `--runs N`..." from the `#### --full flag` section.
4. Remove the `--runs` row from the `audit`/`run` flags table.
5. Remove `--runs`-based examples from the `audit` usage block.

---

## Verification

```bash
# 1. Run the full test suite — must pass with no failures
uv run pytest test_pagespeed_insights_tool.py -v

# 2. Confirm --runs is no longer accepted
pagespeed audit https://example.com --runs 3  # should error: unrecognized arguments

# 3. Smoke-test normal audit still works
pagespeed audit https://example.com

# 4. Smoke-test streaming still works
pagespeed audit https://example.com --stream
```
