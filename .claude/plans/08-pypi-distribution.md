# Plan: Publish to PyPI for `uvx`/`pipx` Distribution

## Context

The tool is currently only usable by cloning the repo and running `uv run pagespeed_insights_tool.py`. The goal is to let users run the tool without cloning — ideally via `uvx pagespeed-insights quick-check https://example.com`. This requires adding a `pyproject.toml`, a PyPI publish workflow, and documentation updates.

An immediate zero-change bonus: `uv run https://raw.githubusercontent.com/volkanunsal/pagespeed/main/pagespeed_insights_tool.py quick-check https://example.com` already works today (PEP 723 magic). We'll document this as a lightweight alternative.

## Files to Create/Modify

| File | Action | Purpose |
|------|--------|---------|
| `pyproject.toml` | Create | Package metadata, build config, `pagespeed` console script entry point |
| `pagespeed_insights_tool.py` | Edit (line 282) | Change `prog=` from `"pagespeed_insights_tool.py"` to `"pagespeed"` |
| `.github/workflows/publish.yml` | Create | Build + publish to PyPI via Trusted Publishing on `v*` tags |
| `.github/workflows/release.yml` | Delete | Superseded by `publish.yml` |
| `.gitignore` | Edit | Add `dist/`, `build/`, `*.egg-info/` |
| `README.md` | Edit | Add installation section leading with `uvx`, update quickstart |
| `CLAUDE.md` | Edit | Document dual-mode operation and release process |

## Step 1: Create `pyproject.toml`

Use **hatchling** as the build backend. Key design choices:

- **Package name**: `pagespeed-insights` (on PyPI)
- **Console script**: `pagespeed` → `pagespeed_insights_tool:main`
- **Version**: Dynamic, read from `__version__` in `pagespeed_insights_tool.py` via `[tool.hatch.version]`
- **Build include**: Only `pagespeed_insights_tool.py` — prevents test file and other artifacts from being packaged
- **PEP 723 metadata stays**: The inline `# /// script` block is just comments to hatchling; it keeps `uv run script.py` working for local dev

```toml
[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[project]
name = "pagespeed-insights"
dynamic = ["version"]
description = "CLI tool for batch Google PageSpeed Insights analysis with CSV/JSON/HTML reports"
readme = "README.md"
license = "MIT"
requires-python = ">=3.13"
keywords = ["pagespeed", "lighthouse", "performance", "web-vitals", "core-web-vitals"]
classifiers = [
    "Development Status :: 4 - Beta",
    "Environment :: Console",
    "License :: OSI Approved :: MIT License",
    "Programming Language :: Python :: 3.13",
    "Topic :: Internet :: WWW/HTTP :: Site Management",
]
dependencies = ["requests", "pandas"]

[project.scripts]
pagespeed = "pagespeed_insights_tool:main"

[project.urls]
Homepage = "https://github.com/volkanunsal/pagespeed"
Repository = "https://github.com/volkanunsal/pagespeed"
Issues = "https://github.com/volkanunsal/pagespeed/issues"

[tool.hatch.version]
path = "pagespeed_insights_tool.py"

[tool.hatch.build]
include = ["pagespeed_insights_tool.py"]
```

## Step 2: Edit `pagespeed_insights_tool.py`

Line 282: change `prog="pagespeed_insights_tool.py"` → `prog="pagespeed"` so help text shows the installed command name.

## Step 3: Create `.github/workflows/publish.yml`

Replaces `release.yml`. Four-job pipeline: **test → build → publish-to-pypi → github-release**.

- Uses `uv build` to create sdist + wheel
- Uses PyPI Trusted Publishing (OIDC, no API tokens stored in secrets)
- GitHub Release still attaches the raw `.py` file for `uv run` URL users
- Requires a one-time manual setup: register pending publisher on PyPI + create `pypi` environment on GitHub

## Step 4: Delete `.github/workflows/release.yml`

All its functionality (test gate, GitHub Release creation) is absorbed into `publish.yml`.

## Step 5: Update `.gitignore`

Append Python build artifact patterns: `dist/`, `build/`, `*.egg-info/`.

## Step 6: Update `README.md`

Replace the "Quickstart" section to lead with `uvx`:

```
## Installation

### Run instantly with `uvx` (recommended, no install needed)
uvx pagespeed-insights quick-check https://example.com

### Install with `pip` or `pipx`
pip install pagespeed-insights
pagespeed quick-check https://example.com

### Run from URL (just needs `uv`)
uv run https://raw.githubusercontent.com/volkanunsal/pagespeed/main/pagespeed_insights_tool.py quick-check ...

### Development
git clone ... && uv run pagespeed_insights_tool.py ...
```

Update all usage examples in the README from `uv run pagespeed_insights_tool.py` → `pagespeed`.

## Step 7: Update `CLAUDE.md`

Add a "Distribution" section documenting:
- PyPI package name and console script mapping
- Dual-mode compatibility (PEP 723 script vs. installed package)
- Requirement to keep both dependency lists in sync
- Release process (bump `__version__`, tag, push)

## One-Time Manual Steps (not automated)

1. **Register on PyPI**: Create pending publisher for `pagespeed-insights` at pypi.org → Publishing → "Add new pending publisher" (owner: `volkanunsal`, repo: `pagespeed`, workflow: `publish.yml`, environment: `pypi`)
2. **Create GitHub environment**: Repo Settings → Environments → "pypi"
3. **Verify package name**: Confirm `pagespeed-insights` is available on PyPI before first publish

## User Experience After Implementation

```bash
# Zero-install, runs from PyPI in an ephemeral env
uvx pagespeed-insights quick-check https://example.com

# Permanent install
pip install pagespeed-insights
pagespeed audit -f urls.txt --strategy both --output-format both

# Works with all subcommands
pagespeed pipeline https://example.com --open
pagespeed compare before.csv after.csv
pagespeed budget results.csv --budget budget.toml
```

## Verification

1. `uv build` — produces `dist/pagespeed_insights-1.1.0.tar.gz` and `.whl`
2. `uv pip install dist/*.whl && pagespeed --version` — prints `pagespeed 1.1.0`
3. `pagespeed quick-check https://example.com` — runs successfully
4. `uv run pagespeed_insights_tool.py quick-check https://example.com` — still works (PEP 723 path)
5. `uv run test_pagespeed_insights_tool.py -v` — all existing tests pass
