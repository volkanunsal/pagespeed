# Plan: Add GitHub Actions CI Workflow

## Context

The project has 126 unit tests across 22 test classes but no CI pipeline. Adding a GitHub Actions workflow ensures tests run automatically on every push and pull request, catching regressions early. All tests are fully mocked (no API keys or network access needed), making CI straightforward.

## Implementation

### Create `.github/workflows/test.yml`

A single workflow file with the following configuration:

**Triggers:**
- Push to `main` branch
- Pull requests targeting `main` branch

**Job: `test`**
- **Runner:** `ubuntu-latest`
- **Steps:**
  1. **Checkout** — `actions/checkout@v4`
  2. **Install uv** — `astral-sh/setup-uv@v5` (official uv installer action)
  3. **Set up Python** — use uv's built-in Python management: `uv python install 3.13`
  4. **Run tests** — `uv run test_pagespeed_insights_tool.py -v`

### Design Decisions

- **Use `uv` directly** rather than `actions/setup-python` + pip. The project uses PEP 723 inline metadata with `uv run`, so this matches the local dev workflow exactly.
- **Single Python version (3.13)** — the project requires `>=3.13` and there's no matrix of versions to support since it's a single-file CLI tool, not a library.
- **No caching config needed** — `astral-sh/setup-uv` handles caching automatically by default.
- **No API key secrets** — all tests mock external I/O.

## Files to Create

- `.github/workflows/test.yml` (new file)

## Verification

After merging, push to `main` or open a PR to confirm:
1. The workflow appears in the repository's Actions tab
2. The job installs uv and Python 3.13
3. All 126 tests pass with `uv run test_pagespeed_insights_tool.py -v`
