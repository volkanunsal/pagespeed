# Plan: Add `pipeline` Subcommand

## Context

Currently, running a full performance analysis from a sitemap requires two separate commands: `audit` (to fetch data and write CSV/JSON) then `report` (to generate an HTML dashboard from the data file). The `pipeline` subcommand chains these into a single invocation — extract URLs, analyze via PageSpeed API, write data files, and generate the HTML report — making the common end-to-end workflow a one-liner.

## Usage Examples

```bash
# Primary: sitemap as positional arg (auto-detected)
uv run pagespeed_insights_tool.py pipeline https://example.com/sitemap.xml

# With options
uv run pagespeed_insights_tool.py pipeline https://example.com/sitemap.xml \
    --sitemap-limit 20 --strategy both --open

# Plain URLs as positional args (fallback)
uv run pagespeed_insights_tool.py pipeline https://a.com https://b.com

# File input
uv run pagespeed_insights_tool.py pipeline -f urls.txt

# Skip HTML report (data-only, same as audit)
uv run pagespeed_insights_tool.py pipeline https://example.com/sitemap.xml --no-report
```

## File to Modify

`pagespeed_insights_tool.py` — the single-file tool (~1447 lines currently)

## Implementation Steps

### Step 1: Extract shared helpers from `cmd_audit()` (lines 933–972)

Two helpers go near `generate_output_path()` (after line 696), to deduplicate logic that both `cmd_audit()` and `cmd_pipeline()` need:

**`_write_data_files(dataframe, output_format, output_dir, explicit_output, strategy_label) -> list[str]`**
- Handles the CSV/JSON writing logic currently at lines 938–958
- Returns list of written file paths

**`_print_audit_summary(dataframe) -> None`**
- Handles the summary printing logic currently at lines 960–972
- Prints avg/min/max scores and error count to stderr

Then refactor `cmd_audit()` (lines 933–972) to call these two helpers instead of inlining the logic.

### Step 2: Add `_looks_like_sitemap()` helper (after line 468)

Small heuristic function to auto-detect whether a positional arg is a sitemap:

```python
def _looks_like_sitemap(source: str) -> bool:
```

Detection rules:
- Ends with `.xml` or `.xml.gz` → sitemap
- Contains "sitemap" (case-insensitive) → sitemap
- Is a local file starting with `<?xml` or containing `<urlset`/`<sitemapindex>` → sitemap
- Otherwise → plain URL

### Step 3: Add `pipeline` parser (after line 291, before `return parser`)

Arguments — mirrors `audit` plus report-specific flags:

| Argument | Notes |
|----------|-------|
| `source` (positional, `nargs="*"`) | Sitemap URL/path or plain URLs |
| `-f/--file` | URL list file |
| `--sitemap` | Explicit sitemap (when positional args are plain URLs) |
| `--sitemap-limit`, `--sitemap-filter` | Sitemap filtering |
| `-s/--strategy` | mobile/desktop/both |
| `--output-format` | csv/json/both |
| `-o/--output`, `--output-dir` | Output paths |
| `-d/--delay`, `-w/--workers` | Rate limiting |
| `--categories` | Lighthouse categories |
| `--open` | Auto-open HTML in browser |
| `--no-report` | Skip HTML generation |

All mutable flags use `TrackingAction`/`TrackingStoreTrueAction`.

### Step 4: Add `cmd_pipeline()` handler (after `cmd_report()`, line 1396)

Five sequential phases:

1. **Resolve sources** — If a single positional arg passes `_looks_like_sitemap()`, route it to `sitemap` param; otherwise treat positional args as plain URLs. `--sitemap` flag always takes precedence.
2. **Load URLs** — Call `load_urls()` (reuse existing function at line 421)
3. **Analyze** — Call `process_urls()` (reuse existing function at line 605) with the in-memory DataFrame result
4. **Write data files + print summary** — Call the helpers from Step 1
5. **Generate HTML report** — Call `generate_html_report()` (line 1077) directly on the in-memory DataFrame, write to file, optionally open in browser. Skip if `--no-report`.

Key design decision: the DataFrame is passed directly from `process_urls()` to `generate_html_report()` — no file roundtrip. This is faster and avoids the timestamp-coordination problem of chaining `audit` then `report`.

### Step 5: Register in dispatch table (line 1430)

Add `"pipeline": cmd_pipeline` to the `commands` dict.

### Step 6: Update `CLAUDE.md`

Add `pipeline` usage example to the "Running the Tool" section and mention the new subcommand in the Architecture section's subcommand handlers list.

## Estimated Size

~135 new lines + ~30 lines refactored in `cmd_audit()`. File grows from ~1447 to ~1580 lines.

## Verification

```bash
# 1. Help text shows pipeline subcommand
uv run pagespeed_insights_tool.py pipeline --help

# 2. Sitemap auto-detection works
uv run pagespeed_insights_tool.py pipeline https://example.com/sitemap.xml --sitemap-limit 2

# 3. Plain URL fallback works
uv run pagespeed_insights_tool.py pipeline https://example.com

# 4. Data files + HTML report all generated in ./reports/
ls reports/

# 5. --no-report skips HTML
uv run pagespeed_insights_tool.py pipeline https://example.com --no-report

# 6. --open launches browser
uv run pagespeed_insights_tool.py pipeline https://example.com --open

# 7. Existing audit command still works (regression check)
uv run pagespeed_insights_tool.py audit https://example.com
```
