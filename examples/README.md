# Examples

Ready-to-use configuration files for common workflows. Copy a folder into your project and edit to taste.

## [`basic/`](basic/)

Minimal setup to get started. Contains a `pagespeed.toml` with common defaults and a sample `urls.txt` file.

```bash
cp -r examples/basic/* .
# Edit pagespeed.toml to add your API key, then:
pagespeed audit -f urls.txt
```

**Files:**
- [`pagespeed.toml`](basic/pagespeed.toml) — API key, strategy, output format
- [`urls.txt`](basic/urls.txt) — sample URL list

## [`multi-profile/`](multi-profile/)

Demonstrates named profiles for switching between workflows with `--profile`. Includes three profiles:

| Profile | Strategy | Format | Categories |
|---------|----------|--------|------------|
| `quick` | mobile | CSV | performance |
| `full` | both | CSV + JSON | all four |
| `client-report` | both | CSV + JSON | performance, accessibility, seo |

```bash
pagespeed audit -f urls.txt --profile quick
pagespeed audit -f urls.txt --profile full
```

**Files:**
- [`pagespeed.toml`](multi-profile/pagespeed.toml) — three named profiles

## [`ci-budget/`](ci-budget/)

Performance budgets for CI pipelines. Two budget files (strict and lenient) plus a CI-oriented config that outputs GitHub Actions annotations.

```bash
# Production gate — exits with code 2 on failure
pagespeed audit -f urls.txt --budget budget-strict.toml

# Dev/staging — catches only major regressions
pagespeed audit -f urls.txt --budget budget-lenient.toml

# Or use the built-in Core Web Vitals preset
pagespeed audit -f urls.txt --budget cwv
```

**Files:**
- [`budget-strict.toml`](ci-budget/budget-strict.toml) — high score thresholds + tight CWV limits
- [`budget-lenient.toml`](ci-budget/budget-lenient.toml) — relaxed thresholds for dev/staging
- [`pagespeed.toml`](ci-budget/pagespeed.toml) — CI config with `budget_format = "github"` and webhook support

## [`sitemap-pipeline/`](sitemap-pipeline/)

End-to-end pipeline that auto-discovers URLs from a sitemap, applies regex filters, and generates an HTML report.

```bash
# Analyze up to 20 URLs from sitemap
pagespeed pipeline https://example.com/sitemap.xml

# Only blog posts
pagespeed pipeline https://example.com/sitemap.xml --profile blog-only

# Only product pages, desktop
pagespeed pipeline https://example.com/sitemap.xml --profile products
```

**Files:**
- [`pagespeed.toml`](sitemap-pipeline/pagespeed.toml) — sitemap limits, regex filters, section-specific profiles
