# Plan 10: Rich TUI — Colors, Panels, and Animated Progress Bar

## Context

The tool's terminal output is entirely plain text: a `\r`-overwritten counter for progress, dotted-line metric tables, unstyled summaries. There is no color, no animation, and retries are completely silent. This plan replaces all of that with a `rich`-powered TUI: animated progress bars with ETA, color-coded performance scores, structured result panels, and styled summaries — while preserving CI/CD safety (rich auto-detects non-TTY and strips all markup automatically).

Version bump: `1.3.0` → `1.4.0`.

---

## Worktree

```bash
git branch --list '[0-9]*'   # should show nothing past 009-*
git worktree add -b 010-rich-tui /tmp/worktrees/010-rich-tui main
```

All edits use absolute paths into `/tmp/worktrees/010-rich-tui/`.

---

## Critical Files

- `pagespeed_insights_tool.py` — all changes live here
- `pyproject.toml` — add `rich` to `[project.dependencies]`

---

## Step 1 — Add `rich` dependency (two places, must stay in sync)

**PEP 723 block** (lines 1–7):
```python
# /// script
# requires-python = ">=3.13"
# dependencies = [
#   "requests",
#   "pandas",
#   "rich",
# ]
# ///
```

**`pyproject.toml`** line 19:
```toml
dependencies = ["requests", "pandas", "rich"]
```

---

## Step 2 — Version bump

```python
__version__ = "1.4.0"
```

---

## Step 3 — Imports

Add after the existing `import requests` line:

```python
from rich.console import Console, Group
from rich.panel import Panel
from rich.progress import (
    BarColumn,
    MofNCompleteColumn,
    Progress,
    SpinnerColumn,
    TaskID,
    TextColumn,
    TimeElapsedColumn,
    TimeRemainingColumn,
)
from rich.table import Table
from rich.text import Text
from rich import box
```

---

## Step 4 — Two module-level Console instances

Place after the `PageSpeedError` class, before the Config section:

```python
# ---------------------------------------------------------------------------
# Rich consoles
# ---------------------------------------------------------------------------

err_console = Console(stderr=True)   # status, progress, summaries → stderr
out_console = Console()              # quick-check results → stdout
```

Two consoles preserve the existing stdout/stderr separation (quick-check results can still be piped).

---

## Step 5 — Score/category styling helpers

Add immediately after the consoles:

```python
def _score_color(score: int | float | None) -> str:
    if score is None:
        return "dim"
    if score >= 90:
        return "green"
    if score >= 50:
        return "yellow"
    return "red"


def _score_text(score: int | float | None) -> Text:
    if score is None:
        return Text("N/A", style="dim")
    label = "GOOD" if score >= 90 else ("NEEDS WORK" if score >= 50 else "POOR")
    return Text(f"{score}/100 ({label})", style=f"bold {_score_color(score)}")


def _field_cat_color(cat: str | None) -> str:
    if cat is None:
        return "dim"
    c = cat.upper()
    if c == "FAST":
        return "green"
    if c == "AVERAGE":
        return "yellow"
    return "red"
```

---

## Step 6 — Rewrite `format_terminal_table()` (lines 1230–1318)

Change return type from `str` to `Group`. Build one `Panel` per (url, strategy) result; each panel wraps a `Table(box=box.SIMPLE)`.

Key structure per result:
- Panel title: `bold cyan` URL + `dim` strategy tag
- Panel border: color from `_score_color(performance_score)`
- Error results: red border, red error message
- Rows: Performance Score (colored via `_score_text`), other category scores, Lab Data section header, lab metrics, Field Data section header, field metrics with category color

Caller update — **only one call site** (line 1404):
```python
# Before:
print(format_terminal_table(results, show_run_metadata=(runs > 1)))
# After:
out_console.print(format_terminal_table(results, show_run_metadata=(runs > 1)))
```

---

## Step 7 — Rewrite `_print_audit_summary()` (lines 943–962)

Replace plain `print` lines with a styled Panel:

```python
def _print_audit_summary(dataframe: pd.DataFrame) -> None:
    if "performance_score" not in dataframe.columns:
        return
    scores = dataframe["performance_score"].dropna()
    if len(scores) == 0:
        return

    t = Table(box=box.SIMPLE, show_header=False, padding=(0, 1))
    t.add_column("Label", style="dim")
    t.add_column("Value")
    t.add_row("URLs analyzed", str(len(dataframe["url"].unique())))
    t.add_row("Avg score", _score_text(round(scores.mean())))
    t.add_row("Min score", _score_text(int(scores.min())))
    t.add_row("Max score", _score_text(int(scores.max())))

    errors = dataframe[dataframe["error"].notna()] if "error" in dataframe.columns else pd.DataFrame()
    if len(errors) > 0:
        t.add_row("Errors", Text(str(len(errors)), style="bold red"))

    if "runs_completed" in dataframe.columns:
        max_runs = dataframe["runs_completed"].max()
        if max_runs > 1:
            t.add_row("Runs/URL", Text(f"{max_runs} (median scoring)", style="dim"))

    err_console.print(Panel(t, title="Summary", border_style="blue"))
```

---

## Step 8 — Style `_write_data_files()` output (lines 936–938)

```python
# Before:
print(f"\nResults written to:", file=sys.stderr)
for filepath in written_files:
    print(f"  {filepath}", file=sys.stderr)

# After:
err_console.print("")
for filepath in written_files:
    err_console.print(f"  [green]✓[/green] [cyan]{filepath}[/cyan]")
```

---

## Step 9 — Surface retry warnings in `fetch_pagespeed_result()` (lines 640–667)

In the retryable HTTP error branch, before `time.sleep()`:
```python
err_console.print(
    f"  [yellow]⚠[/yellow] HTTP {response.status_code} — retrying in {wait_time:.1f}s "
    f"(attempt {attempt + 1}/{MAX_RETRIES})..."
)
```

In the `requests.RequestException` branch, add `wait_time` assignment and warning before `time.sleep()`:
```python
wait_time = RETRY_BASE_DELAY * (2**attempt)
err_console.print(
    f"  [yellow]⚠[/yellow] Request error: {exc} — retrying in {wait_time:.1f}s "
    f"(attempt {attempt + 1}/{MAX_RETRIES})..."
)
time.sleep(wait_time)
```

---

## Step 10 — Add spinner to `cmd_quick_check()` (lines 1386–1404)

Wrap each individual fetch in `err_console.status()`. Convert error/validation prints to `err_console.print()`. Keep final output on `out_console.print()`:

```python
for strategy in strategies:
    run_metrics = []
    for run_number in range(1, runs + 1):
        run_label = f" [run {run_number}/{runs}]" if runs > 1 else ""
        with err_console.status(
            f"Fetching [cyan]{url}[/cyan] ({strategy}){run_label}...",
            spinner="dots",
        ):
            try:
                response = fetch_pagespeed_result(url, strategy, args.api_key, categories)
                metrics = extract_metrics(response, url, strategy)
                run_metrics.append(metrics)
            except PageSpeedError as exc:
                run_metrics.append({"url": url, "strategy": strategy, "error": str(exc)})
```

---

## Step 11 — Replace `process_urls()` progress with rich `Progress` (lines 734–819)

This is the highest-risk change — test carefully with both single-worker and multi-worker configs.

**Key design decisions:**
- `transient=True` — bar clears on completion, leaving terminal clean for summary panel below
- Progress bar is created in outer scope, referenced via closure in `process_single()`
- `Progress.advance()` is thread-safe; no lock needed around it
- Remove `completed_count` and the old `with lock:` block (only existed for the `\r` print)
- Remove the `lock` object entirely (semaphore still needed for rate limiting)
- Description updates (`progress.update(..., description=...)`) happen before the HTTP call, giving live "currently fetching X" feedback

```python
def process_urls(...) -> pd.DataFrame:
    results: list[dict] = []
    base_tasks = [(url, strategy) for url in urls for strategy in strategies]
    task_list = base_tasks * runs
    total_tasks = len(task_list)
    base_count = len(base_tasks)
    semaphore = threading.Semaphore(1)
    last_request_time = [0.0]
    results_lock = threading.Lock()  # protects results list append only

    progress = Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        MofNCompleteColumn(),
        TimeElapsedColumn(),
        TimeRemainingColumn(),
        console=err_console,
        transient=True,
    )
    prog_task = progress.add_task("Fetching...", total=total_tasks)

    def process_single(url: str, strategy: str, task_index: int) -> dict:
        with semaphore:
            now = time.monotonic()
            elapsed = now - last_request_time[0]
            if elapsed < delay:
                time.sleep(delay - elapsed)
            last_request_time[0] = time.monotonic()

        short_url = url if len(url) <= 50 else url[:47] + "..."
        run_label = f" run {task_index // base_count + 1}/{runs}" if runs > 1 else ""
        progress.update(prog_task, description=f"[cyan]{short_url}[/cyan] ({strategy}){run_label}")

        if verbose:
            v_run_label = f" [run {task_index // base_count + 1}/{runs}]" if runs > 1 else ""
            err_console.print(f"  [dim]Fetching[/dim] [cyan]{url}[/cyan] ({strategy}){v_run_label}...")

        try:
            response = fetch_pagespeed_result(url, strategy, api_key, categories)
            metrics = extract_metrics(response, url, strategy)
        except PageSpeedError as exc:
            metrics = {"url": url, "strategy": strategy, "error": str(exc)}
            err_console.print(f"  [bold red]Error:[/bold red] {exc}")

        progress.advance(prog_task)
        return metrics

    with progress:
        effective_workers = min(workers, total_tasks)
        if effective_workers <= 1:
            for task_index, (url, strategy) in enumerate(task_list):
                results.append(process_single(url, strategy, task_index))
        else:
            with ThreadPoolExecutor(max_workers=effective_workers) as executor:
                futures = {
                    executor.submit(process_single, url, strategy, task_index): (url, strategy)
                    for task_index, (url, strategy) in enumerate(task_list)
                }
                for future in as_completed(futures):
                    with results_lock:
                        results.append(future.result())

    raw_dataframe = pd.DataFrame(results)
    return aggregate_multi_run(raw_dataframe, runs)
```

Note: remove the old `print("", file=sys.stderr)` at line 817 — `transient=True` handles cleanup.

---

## Step 12 — Style status messages in `cmd_audit()` and `cmd_pipeline()`

Convert `print(..., file=sys.stderr)` calls to `err_console.print(...)` with markup:

```python
# cmd_audit() line 1430:
err_console.print(
    f"Auditing [bold]{len(urls)}[/bold] URL(s) · strategy: [cyan]{args.strategy}[/cyan]{runs_label}"
)

# cmd_pipeline() line 1935:
err_console.print(
    f"Pipeline: analyzing [bold]{len(urls)}[/bold] URL(s) · strategy: [cyan]{args.strategy}[/cyan]{runs_label}"
)

# cmd_pipeline() HTML report confirmation:
err_console.print(f"  [green]✓[/green] HTML report: [cyan]{html_path}[/cyan]")

# --runs validation errors in both commands:
err_console.print("[bold red]Error:[/bold red] --runs must be at least 1")
```

---

## Step 13 — Sweep remaining `print(..., file=sys.stderr)` calls

Convert any remaining raw `print(..., file=sys.stderr)` to `err_console.print(...)`. Key areas:
- Sitemap loading verbose messages in `load_urls_from_sitemap()`
- Config warning messages in `load_config()`
- Budget webhook error in `send_budget_webhook()`
- Error prints in `load_report()`

**Do not touch:** `format_budget_text()`, `format_budget_json()`, `format_budget_github()` — these return plain strings used in CI pipelines.

---

## Merge & Cleanup

```bash
# From main worktree:
git merge 010-rich-tui
git add .claude/plans/10-rich-tui.md
git commit -m "docs: add plan for rich TUI"
git worktree remove /tmp/worktrees/010-rich-tui
git branch -d 010-rich-tui
```

---

## Verification

```bash
# 1. Import check
python -c "import pagespeed_insights_tool"

# 2. Version
pagespeed --version  # → 1.4.0

# 3. Spinner + colored panel (quick-check)
pagespeed quick-check https://example.com

# 4. Multi-run spinner
pagespeed quick-check https://example.com --runs 3

# 5. Animated progress bar + summary panel (audit)
pagespeed audit -f urls.txt --strategy mobile

# 6. Pipeline with HTML confirmation
pagespeed pipeline https://example.com

# 7. Non-TTY: no ANSI escapes on stdout
pagespeed quick-check https://example.com | cat | python -c "import sys; data=sys.stdin.read(); assert '\x1b' not in data, 'ANSI found on stdout'"

# 8. CI simulation: plain text on piped stderr
pagespeed audit -f urls.txt 2>&1 | cat

# 9. Budget output still plain
pagespeed audit -f urls.txt --budget budget.toml --budget-format github 2>&1 | grep "::"
```
