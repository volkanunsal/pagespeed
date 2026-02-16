# Plan: Add Unit Tests for pagespeed_insights_tool.py

## Context

The project is a single-file Python CLI tool (`pagespeed_insights_tool.py`, ~1448 lines) with zero tests. Adding a comprehensive `unittest`-based test suite will ensure reliability, catch regressions, and enable safe refactoring. All external I/O (API calls, sitemap fetches, file reads) will be mocked so tests run fast and offline.

## Files to Create/Modify

- **Create:** `test_pagespeed_insights_tool.py` — the test suite (18 test classes, ~102 test methods)
- **Modify:** `README.md` — add a "Testing" section after the existing content

## Test Architecture

### Running Tests

```bash
uv run python -m unittest test_pagespeed_insights_tool.py -v
```

### Import Strategy

```python
import pagespeed_insights_tool as pst
```

Since `uv run` manages the venv, tests can import the module directly.

### Shared Test Fixtures (module-level constants)

1. **`FULL_API_RESPONSE`** — realistic API response with all lighthouse categories, all 6 lab metrics, all 6 field metrics, and `fetchTime`
2. **`MINIMAL_API_RESPONSE`** — only `lighthouseResult.categories.performance.score`, everything else absent
3. **`SAMPLE_SITEMAP_URLSET`** — valid sitemap XML with xmlns namespace (3 URLs)
4. **`SAMPLE_SITEMAP_URLSET_NO_NS`** — same without namespace
5. **`SAMPLE_SITEMAP_INDEX`** — sitemapindex XML referencing 2 child sitemaps
6. **`SAMPLE_DATAFRAME`** — 2-row DataFrame (mobile + desktop) with realistic metrics

### Helper

```python
def _make_mock_response(status_code, json_data=None, headers=None, text=""):
    """Factory for mock requests.Response objects."""
```

## Test Classes (18 classes, ~102 methods)

### Pure functions (no mocking)

| # | Class | Tests | Source Lines | Key Scenarios |
|---|-------|-------|-------------|---------------|
| 1 | `TestValidateUrl` | 8 | 300-313 | Valid https/http, schemeless gets https://, empty/comment/no-TLD return None, whitespace stripped, complex URLs preserved |
| 2 | `TestExtractMetrics` | 8 | 546-597 | Full extraction, score*100, category scores, CLS rounding (lab: 4 decimals, field: percentile/100), missing data graceful, None score preserved |
| 3 | `TestFormatTerminalTable` | 6 | 754-828 | Single dict, list input, error row, score indicators (GOOD/NEEDS WORK/POOR), field data section conditional |
| 4 | `TestGenerateHtmlReport` | 7 | 1077-1371 | DOCTYPE/HTML tags, URLs present, score color classes, CWV pass/fail indicators, field section conditional, error rows |

### Config and argument parsing

| # | Class | Tests | Source Lines | Key Scenarios |
|---|-------|-------|-------------|---------------|
| 5 | `TestLoadConfig` | 4 | 126-138 | None path returns {}, valid TOML parsed, malformed exits, unreadable exits |
| 6 | `TestApplyProfile` | 7 | 141-197 | Empty config preserves defaults, settings fill unset, profile overrides settings, CLI explicit overrides all, missing profile exits, env var fallback, config api_key not overridden by env |
| 7 | `TestDiscoverConfigPath` | 3 | 116-123 | No config returns None, CWD config found, home config found |
| 8 | `TestTrackingAction` | 3 | 205-225 | TrackingAction records dest, TrackingStoreTrueAction records dest, unset flags not tracked |
| 9 | `TestBuildArgumentParser` | 5 | 228-292 | Each subcommand parses correctly, default values correct |

### Sitemap handling (mock `_fetch_sitemap_content`)

| # | Class | Tests | Source Lines | Key Scenarios |
|---|-------|-------|-------------|---------------|
| 10 | `TestParseSitemapXml` | 8 | 326-374 | Namespaced urlset, no-namespace urlset, sitemapindex recursive fetch, child fetch failure, max depth, malformed XML, empty locs, no-namespace index |
| 11 | `TestFetchSitemapUrls` | 5 | 377-418 | Basic fetch+parse, regex filter, limit, invalid regex returns empty, fetch failure returns empty |

### URL loading (mock file I/O, stdin, `fetch_sitemap_urls`)

| # | Class | Tests | Source Lines | Key Scenarios |
|---|-------|-------|-------------|---------------|
| 12 | `TestLoadUrls` | 9 | 421-468 | From args, from file, from stdin, from sitemap, deduplication, invalid URLs skipped with warning, no valid URLs exits, file not found exits, args takes priority over file |

### API client (mock `requests.get`, `time.sleep`)

| # | Class | Tests | Source Lines | Key Scenarios |
|---|-------|-------|-------------|---------------|
| 13 | `TestFetchPagespeedResult` | 7 | 476-538 | Success first attempt, 429 with Retry-After, 500 exponential backoff, 503, non-retryable error (403), max retries exhausted, request exception retried |

### Batch processing (mock `fetch_pagespeed_result`, `time.sleep`, `time.monotonic`)

| # | Class | Tests | Source Lines | Key Scenarios |
|---|-------|-------|-------------|---------------|
| 14 | `TestProcessUrls` | 5 | 605-676 | Single URL, multiple URLs+strategies, error handling per URL, sequential (workers=1), concurrent (workers=4) |

### Output (temp directories)

| # | Class | Tests | Source Lines | Key Scenarios |
|---|-------|-------|-------------|---------------|
| 15 | `TestGenerateOutputPath` | 3 | 684-689 | Path format, creates directory, different strategies/extensions |
| 16 | `TestOutputCsv` | 3 | 692-696 | Writes file, returns path string, creates parent directory |
| 17 | `TestOutputJson` | 5 | 699-751 | Writes file, metadata envelope, results structure, nested lab/field metrics |
| 18 | `TestLoadReport` | 6 | 836-873 | Load CSV, load structured JSON, load flat JSON, file not found exits, unsupported format exits, CSV/JSON round-trip |

## Mocking Strategy

| Dependency | Patch Target | Used In |
|-----------|-------------|---------|
| `requests.get` | `pagespeed_insights_tool.requests.get` | TestFetchPagespeedResult |
| `time.sleep` | `pagespeed_insights_tool.time.sleep` | TestFetchPagespeedResult, TestProcessUrls |
| `time.monotonic` | `pagespeed_insights_tool.time.monotonic` | TestProcessUrls |
| `_fetch_sitemap_content` | `pagespeed_insights_tool._fetch_sitemap_content` | TestParseSitemapXml, TestFetchSitemapUrls |
| `fetch_sitemap_urls` | `pagespeed_insights_tool.fetch_sitemap_urls` | TestLoadUrls |
| `fetch_pagespeed_result` | `pagespeed_insights_tool.fetch_pagespeed_result` | TestProcessUrls |
| `sys.stdin` | `sys.stdin` | TestLoadUrls |
| `os.environ` | `@patch.dict(os.environ, ...)` | TestApplyProfile |
| `datetime.now` | `pagespeed_insights_tool.datetime` | TestGenerateOutputPath |
| File I/O | `tempfile.NamedTemporaryFile`, `tempfile.TemporaryDirectory` | Multiple |

## Edge Cases to Handle Carefully

1. **CLS rounding divergence** — lab CLS: `round(value, 4)` on raw float; field CLS: `round(percentile / 100, 4)` where percentile is integer
2. **Retry boundary** — `range(MAX_RETRIES + 1)` = 4 attempts total; only 429 uses `Retry-After` header
3. **`load_urls` source priority** — `url_args` vs `file_path` vs stdin are `elif` (mutually exclusive), but `sitemap` always extends
4. **`process_urls` code paths** — workers=1 uses sequential `for` loop; workers>1 uses `ThreadPoolExecutor`

## README Changes

Add a "Testing" section to `README.md` after the existing content (before any footer), documenting:
- How to run all tests: `uv run python -m unittest test_pagespeed_insights_tool.py -v`
- How to run a single test class: `uv run python -m unittest test_pagespeed_insights_tool.TestValidateUrl -v`
- Note that all tests run offline (API calls are mocked)

## Implementation Order

1. Write shared fixtures and helper functions
2. Pure function tests (TestValidateUrl, TestExtractMetrics, TestFormatTerminalTable)
3. Config/parser tests (TestLoadConfig, TestApplyProfile, TestDiscoverConfigPath, TestTrackingAction, TestBuildArgumentParser)
4. Sitemap tests (TestParseSitemapXml, TestFetchSitemapUrls)
5. URL loading tests (TestLoadUrls)
6. API client tests (TestFetchPagespeedResult)
7. Batch processing tests (TestProcessUrls)
8. Output tests (TestGenerateOutputPath, TestOutputCsv, TestOutputJson, TestLoadReport)
9. HTML report tests (TestGenerateHtmlReport)
10. README update

## Verification

```bash
# Run all tests
uv run python -m unittest test_pagespeed_insights_tool.py -v

# Verify test count (~102 tests expected)
uv run python -m unittest test_pagespeed_insights_tool.py 2>&1 | tail -1

# Run a single class to spot-check
uv run python -m unittest test_pagespeed_insights_tool.TestFetchPagespeedResult -v
```

## Worktree Workflow

Per CLAUDE.md, this should be implemented in a worktree branch:
1. Check `git branch --list '[0-9]*'` for next sequence number
2. Create worktree: `git worktree add -b NNN-add-unit-tests /tmp/worktrees/NNN-add-unit-tests main`
3. All edits use absolute paths into `/tmp/worktrees/NNN-add-unit-tests/`
4. Commits via `git -C /tmp/worktrees/NNN-add-unit-tests/ ...`
5. Merge back to main, then cleanup
