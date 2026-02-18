# Plan: Refactor to asyncio

## Context

The tool currently uses `requests` + `ThreadPoolExecutor` + `threading.Semaphore/Lock` for concurrent URL auditing. Moving to `asyncio` + `httpx` eliminates thread overhead, makes the concurrency model more explicit, and enables better pipelining: while one coroutine awaits a response, others can be sleeping through their rate-limit delay or awaiting their own responses — all on a single thread without the GIL contention of `ThreadPoolExecutor`.

The existing rate-limiting semantics (`Semaphore(1)` + delay timer) are preserved exactly; only the implementation primitives change.

## Dependency changes

Two files must stay in sync (CLAUDE.md: "Dependency sync"):

1. **PEP 723 block** (lines 1-8, `pagespeed_insights_tool.py`): replace `"requests"` → `"httpx"`
2. **`pyproject.toml`** (`[project.dependencies]`, line 19): replace `"requests"` → `"httpx"`

## Import changes (`pagespeed_insights_tool.py`, lines 16-50)

- Remove: `import threading`, `from concurrent.futures import ThreadPoolExecutor, as_completed`, `import requests`
- Add: `import asyncio`, `import httpx`
- Keep: `import time` (still used for `time.monotonic()`)

## Function-by-function changes

### `_fetch_sitemap_content()` (lines 476-483)
- `async def`
- `requests.get(...)` → `await client.get(...)` where `client: httpx.AsyncClient` is passed in as a parameter

### `fetch_sitemap_urls()` (lines 537-578)
- `async def`
- `await _fetch_sitemap_content(source, client)`

### `fetch_pagespeed_result()` (lines 661-732)
- `async def`
- Add parameter `client: httpx.AsyncClient`
- `requests.get(...)` → `await client.get(...)`
- `requests.RequestException` → `httpx.RequestError`
- Both `time.sleep(wait_time)` calls (lines 707, 729) → `await asyncio.sleep(wait_time)`

### `send_budget_webhook()` (lines 1178-1184)
- `async def`
- `requests.post(...)` → `await client.post(...)` — pass a shared `client: httpx.AsyncClient`

### `process_urls()` (lines 799-879) — core change
- `async def`
- Create a single `async with httpx.AsyncClient() as client:` context wrapping the entire function body (connection reuse across all requests)
- `threading.Semaphore(1)` → `asyncio.Semaphore(1)`
- Remove `threading.Lock()` (no longer needed: asyncio is single-threaded, results list is safe to append without a lock)
- `process_single` inner function → `async def process_single`
- Inside `process_single`: `time.sleep(...)` → `await asyncio.sleep(...)`
- `fetch_pagespeed_result(...)` → `await fetch_pagespeed_result(..., client=client)`
- Sequential path (`effective_workers <= 1`): `for` loop with `await process_single(...)` — preserved as-is
- Concurrent path: replace `ThreadPoolExecutor` + `as_completed` with `results = list(await asyncio.gather(*[process_single(url, strategy, i) for i, (url, strategy) in enumerate(task_list)]))`
- `aggregate_multi_run()` call is unchanged (sync/CPU-bound, fine to call from async)

### Subcommand handlers
- `cmd_quick_check()`: `async def`; `fetch_pagespeed_result(...)` → `await fetch_pagespeed_result(..., client=client)` inside an `async with httpx.AsyncClient() as client:` block
- `cmd_audit()`: `async def`; `process_urls(...)` → `await process_urls(...)`
- `cmd_run()`: `async def`; delegates to `await cmd_audit(...)`
- `cmd_pipeline()`: `async def`; `fetch_sitemap_urls(...)` → `await fetch_sitemap_urls(...)`; `process_urls(...)` → `await process_urls(...)`; `send_budget_webhook(...)` → `await send_budget_webhook(...)`
- `cmd_budget()`: `async def` only if it calls `send_budget_webhook`; otherwise sync OK
- `cmd_compare()`, `cmd_report()`: no change (no I/O requiring async)

### `main()` (lines 2072-2104)
- Keep `def main()` synchronous (entry point for PyPI console script must be sync)
- Detect async handlers and dispatch via `asyncio.run()`:
  ```python
  handler = commands.get(args.command)
  if asyncio.iscoroutinefunction(handler):
      asyncio.run(handler(args))
  else:
      handler(args)
  ```

## What does NOT change

- `aggregate_multi_run()` — pure pandas, CPU-bound, sync OK
- `output_csv()`, `output_json()`, `generate_html_report()` — file writes are fast/infrequent; no need for `aiofiles`
- Rich `Progress` — already thread-safe, works unchanged in async context
- Rate-limiting semantics — `asyncio.Semaphore(1)` + `asyncio.sleep()` preserves identical behaviour
- `--workers` flag semantics — sequential vs concurrent path is preserved (workers=1 → for loop, workers>1 → gather)
- Task ordering (interleaved multi-run) — `task_list = base_tasks * runs` unchanged

## File change summary

| File | Change |
|------|--------|
| `pagespeed_insights_tool.py` | Replace imports; make ~8 functions async; swap threading/requests primitives |
| `pyproject.toml` | `requests` → `httpx` in `[project.dependencies]` |

## Verification

```bash
# Syntax check
python3 -m py_compile pagespeed_insights_tool.py

# Single URL, quick sanity check
uv run pagespeed_insights_tool.py quick-check https://example.com

# Multi-worker concurrent path
uv run pagespeed_insights_tool.py audit -f urls.txt --workers 4

# Sequential path
uv run pagespeed_insights_tool.py audit -f urls.txt --workers 1

# Multi-run path
uv run pagespeed_insights_tool.py quick-check https://example.com --runs 3

# Pipeline with sitemap (tests async sitemap fetch)
uv run pagespeed_insights_tool.py pipeline https://example.com/sitemap.xml --sitemap-limit 5
```
