# /// script
# requires-python = ">=3.13"
# dependencies = [
#   "httpx",
#   "pandas",
#   "rich",
# ]
# ///
"""PageSpeed Insights Batch Analysis CLI Tool.

Automates Google PageSpeed Insights analysis across multiple URLs,
extracting performance metrics (lab + field data) into structured
CSV/JSON/HTML reports.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import os
import re
import sys
import textwrap
import time
import tomllib
import webbrowser
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse
import xml.etree.ElementTree as ET

import httpx
import pandas as pd
from rich.console import Console, Group
from rich.panel import Panel
from rich.progress import (
    BarColumn,
    MofNCompleteColumn,
    Progress,
    SpinnerColumn,
    TextColumn,
    TimeElapsedColumn,
    TimeRemainingColumn,
)
from rich.table import Table
from rich.text import Text
from rich import box

__version__ = "1.6.0"

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

PAGESPEED_API_URL = "https://www.googleapis.com/pagespeedonline/v5/runPagespeed"

VALID_STRATEGIES = ("mobile", "desktop", "both")
VALID_CATEGORIES = ("performance", "accessibility", "best-practices", "seo")
VALID_OUTPUT_FORMATS = ("csv", "json", "both")

DEFAULT_DELAY = 1.5
DEFAULT_WORKERS = 4
DEFAULT_STRATEGY = "mobile"
DEFAULT_OUTPUT_FORMAT = "csv"
DEFAULT_OUTPUT_DIR = "./reports"
DEFAULT_CATEGORIES = ["performance"]

MAX_RETRIES = 3
RETRY_BASE_DELAY = 2.0
RETRYABLE_STATUS_CODES = {429, 500, 503}

CONFIG_FILENAMES = ["pagespeed.toml"]
CONFIG_SEARCH_PATHS = [
    Path.cwd(),
    Path.home() / ".config" / "pagespeed",
]

# Lab metrics: (audit_id, output_column_name)
LAB_METRICS = [
    ("first-contentful-paint", "lab_fcp_ms"),
    ("largest-contentful-paint", "lab_lcp_ms"),
    ("cumulative-layout-shift", "lab_cls"),
    ("speed-index", "lab_speed_index_ms"),
    ("total-blocking-time", "lab_tbt_ms"),
    ("interactive", "lab_tti_ms"),
]

# Field metrics: (api_key, output_value_column, output_category_column)
FIELD_METRICS = [
    ("FIRST_CONTENTFUL_PAINT_MS", "field_fcp_ms", "field_fcp_category"),
    ("LARGEST_CONTENTFUL_PAINT_MS", "field_lcp_ms", "field_lcp_category"),
    ("CUMULATIVE_LAYOUT_SHIFT_SCORE", "field_cls", "field_cls_category"),
    ("INTERACTION_TO_NEXT_PAINT", "field_inp_ms", "field_inp_category"),
    ("FIRST_INPUT_DELAY_MS", "field_fid_ms", "field_fid_category"),
    ("EXPERIMENTAL_TIME_TO_FIRST_BYTE", "field_ttfb_ms", "field_ttfb_category"),
]

# Core Web Vitals thresholds for HTML report
CWV_THRESHOLDS = {
    "lab_lcp_ms": {"good": 2500, "poor": 4000, "unit": "ms", "label": "LCP"},
    "lab_cls": {"good": 0.1, "poor": 0.25, "unit": "", "label": "CLS"},
    "lab_tbt_ms": {"good": 200, "poor": 600, "unit": "ms", "label": "TBT"},
    "lab_fcp_ms": {"good": 1800, "poor": 3000, "unit": "ms", "label": "FCP"},
    "field_lcp_ms": {"good": 2500, "poor": 4000, "unit": "ms", "label": "LCP"},
    "field_cls": {"good": 0.1, "poor": 0.25, "unit": "", "label": "CLS"},
    "field_inp_ms": {"good": 200, "poor": 500, "unit": "ms", "label": "INP"},
}

SITEMAP_NS = {"sm": "http://www.sitemaps.org/schemas/sitemap/0.9"}
SITEMAP_FETCH_TIMEOUT = 30
MAX_SITEMAP_DEPTH = 3

# Budget evaluation constants
BUDGET_EXIT_CODE = 2

BUDGET_METRIC_MAP = {
    "min_performance_score":   ("performance_score",   ">="),
    "min_accessibility_score": ("accessibility_score", ">="),
    "min_best_practices_score": ("best_practices_score", ">="),
    "min_seo_score":           ("seo_score",           ">="),
    "max_lcp_ms":              ("lab_lcp_ms",          "<="),
    "max_cls":                 ("lab_cls",             "<="),
    "max_tbt_ms":              ("lab_tbt_ms",          "<="),
    "max_fcp_ms":              ("lab_fcp_ms",          "<="),
}

CWV_BUDGET_PRESET = {
    "max_lcp_ms": CWV_THRESHOLDS["lab_lcp_ms"]["good"],
    "max_cls":    CWV_THRESHOLDS["lab_cls"]["good"],
    "max_tbt_ms": CWV_THRESHOLDS["lab_tbt_ms"]["good"],
    "max_fcp_ms": CWV_THRESHOLDS["lab_fcp_ms"]["good"],
}


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class PageSpeedError(Exception):
    """Raised when a PageSpeed API request fails after all retries."""


# ---------------------------------------------------------------------------
# Rich consoles
# ---------------------------------------------------------------------------

err_console = Console(stderr=True)   # status, progress, summaries → stderr
out_console = Console()              # quick-check results → stdout


# ---------------------------------------------------------------------------
# Score/category styling helpers
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# Config & Profile
# ---------------------------------------------------------------------------


def discover_config_path() -> Path | None:
    """Find the first existing config file in search paths."""
    for search_dir in CONFIG_SEARCH_PATHS:
        for filename in CONFIG_FILENAMES:
            candidate = search_dir / filename
            if candidate.is_file():
                return candidate
    return None


def load_config(config_path: Path | None) -> dict:
    """Parse a TOML config file and return its contents as a dict."""
    if config_path is None:
        return {}
    try:
        with open(config_path, "rb") as fh:
            return tomllib.load(fh)
    except tomllib.TOMLDecodeError as exc:
        err_console.print(f"[bold red]Error:[/bold red] malformed config file {config_path}: {exc}")
        sys.exit(1)
    except OSError as exc:
        err_console.print(f"[bold red]Error:[/bold red] cannot read config file {config_path}: {exc}")
        sys.exit(1)


def load_budget(budget_source: str) -> dict:
    """Load a performance budget from a TOML file or built-in preset.

    If budget_source is "cwv", returns the Core Web Vitals preset.
    Otherwise, reads and parses a TOML file.
    """
    if budget_source == "cwv":
        return {"thresholds": CWV_BUDGET_PRESET, "meta": {"name": "Core Web Vitals"}}

    budget_path = Path(budget_source)
    if not budget_path.is_file():
        err_console.print(f"[bold red]Error:[/bold red] budget file not found: {budget_source}")
        sys.exit(1)
    try:
        with open(budget_path, "rb") as fh:
            data = tomllib.load(fh)
    except tomllib.TOMLDecodeError as exc:
        err_console.print(f"[bold red]Error:[/bold red] malformed budget file {budget_source}: {exc}")
        sys.exit(1)

    return {
        "thresholds": data.get("thresholds", {}),
        "meta": data.get("meta", {}),
    }


def apply_profile(args: argparse.Namespace, config: dict, profile_name: str | None) -> argparse.Namespace:
    """Merge config [settings] and optional profile into args.

    Resolution order (highest priority wins):
      1. Explicit CLI flags
      2. Profile values
      3. [settings] defaults from config
      4. Built-in defaults (already in args)
    """
    settings = config.get("settings", {})
    profile = {}
    if profile_name:
        profiles = config.get("profiles", {})
        if profile_name not in profiles:
            available = ", ".join(profiles.keys()) if profiles else "(none)"
            print(
                f"Error: profile '{profile_name}' not found in config. Available: {available}",
                file=sys.stderr,
            )
            sys.exit(1)
        profile = profiles[profile_name]

    # Map config keys to argparse dest names
    config_key_map = {
        "api_key": "api_key",
        "urls_file": "file",
        "delay": "delay",
        "strategy": "strategy",
        "output_format": "output_format",
        "output_dir": "output_dir",
        "workers": "workers",
        "categories": "categories",
        "verbose": "verbose",
        "sitemap": "sitemap",
        "sitemap_limit": "sitemap_limit",
        "sitemap_filter": "sitemap_filter",
        "budget": "budget",
        "budget_format": "budget_format",
        "webhook_url": "webhook",
        "webhook_on": "webhook_on",
    }

    # Track which args were explicitly set on the CLI
    cli_explicit = set(getattr(args, "_explicit_args", []))

    for config_key, arg_dest in config_key_map.items():
        if arg_dest in cli_explicit:
            continue  # CLI flag takes priority
        # Try profile first, then settings
        if config_key in profile:
            setattr(args, arg_dest, profile[config_key])
        elif config_key in settings:
            setattr(args, arg_dest, settings[config_key])

    # Resolve API key from env if not set anywhere
    if not getattr(args, "api_key", None):
        env_key = os.environ.get("PAGESPEED_API_KEY")
        if env_key:
            args.api_key = env_key

    return args


# ---------------------------------------------------------------------------
# CLI Argument Parser
# ---------------------------------------------------------------------------


class TrackingAction(argparse.Action):
    """Argparse action that records which flags were explicitly provided."""

    def __call__(self, parser, namespace, values, option_string=None):
        setattr(namespace, self.dest, values)
        explicit = getattr(namespace, "_explicit_args", [])
        explicit.append(self.dest)
        namespace._explicit_args = explicit


class TrackingStoreTrueAction(argparse.Action):
    """Like store_true but tracks that the flag was explicitly set."""

    def __init__(self, option_strings, dest, default=False, required=False, help=None):
        super().__init__(option_strings=option_strings, dest=dest, nargs=0, const=True, default=default, required=required, help=help)

    def __call__(self, parser, namespace, values, option_string=None):
        setattr(namespace, self.dest, True)
        explicit = getattr(namespace, "_explicit_args", [])
        explicit.append(self.dest)
        namespace._explicit_args = explicit


def build_argument_parser() -> argparse.ArgumentParser:
    """Build the CLI argument parser with subcommands."""
    parser = argparse.ArgumentParser(
        prog="pagespeed",
        description="PageSpeed Insights Batch Analysis CLI Tool",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    parser.add_argument("--api-key", dest="api_key", action=TrackingAction, default=None, help="Google API key (or set PAGESPEED_API_KEY env var)")
    parser.add_argument("-c", "--config", dest="config", action=TrackingAction, default=None, help="Path to config TOML file")
    parser.add_argument("-p", "--profile", dest="profile", action=TrackingAction, default=None, help="Named profile from config file")
    parser.add_argument("-v", "--verbose", dest="verbose", action=TrackingStoreTrueAction, default=False, help="Verbose output to stderr")

    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    # --- quick-check ---
    quick_check_parser = subparsers.add_parser("quick-check", help="Fast single-URL spot check")
    quick_check_parser.add_argument("url", help="URL to check")
    quick_check_parser.add_argument("-s", "--strategy", dest="strategy", action=TrackingAction, default=DEFAULT_STRATEGY, choices=VALID_STRATEGIES, help="Strategy: mobile, desktop, or both")
    quick_check_parser.add_argument("--categories", dest="categories", action=TrackingAction, nargs="+", default=DEFAULT_CATEGORIES, choices=VALID_CATEGORIES, help="Lighthouse categories")

    # --- audit ---
    audit_parser = subparsers.add_parser("audit", help="Full batch analysis with report output")
    audit_parser.add_argument("urls", nargs="*", default=[], help="URLs to audit")
    audit_parser.add_argument("-f", "--file", dest="file", action=TrackingAction, default=None, help="File with one URL per line")
    audit_parser.add_argument("--sitemap", dest="sitemap", action=TrackingAction, default=None, help="URL or local path to sitemap.xml")
    audit_parser.add_argument("--sitemap-limit", dest="sitemap_limit", action=TrackingAction, type=int, default=None, help="Max URLs to extract from sitemap")
    audit_parser.add_argument("--sitemap-filter", dest="sitemap_filter", action=TrackingAction, default=None, help="Regex to filter sitemap URLs")
    audit_parser.add_argument("-s", "--strategy", dest="strategy", action=TrackingAction, default=DEFAULT_STRATEGY, choices=VALID_STRATEGIES, help="Strategy: mobile, desktop, or both")
    audit_parser.add_argument("--output-format", dest="output_format", action=TrackingAction, default=DEFAULT_OUTPUT_FORMAT, choices=VALID_OUTPUT_FORMATS, help="Output format: csv, json, or both")
    audit_parser.add_argument("-o", "--output", dest="output", action=TrackingAction, default=None, help="Explicit output file path (overrides auto-naming)")
    audit_parser.add_argument("--output-dir", dest="output_dir", action=TrackingAction, default=DEFAULT_OUTPUT_DIR, help="Directory for auto-named output files")
    audit_parser.add_argument("-d", "--delay", dest="delay", action=TrackingAction, type=float, default=DEFAULT_DELAY, help="Seconds between API requests")
    audit_parser.add_argument("-w", "--workers", dest="workers", action=TrackingAction, type=int, default=DEFAULT_WORKERS, help="Concurrent workers (1 = sequential)")
    audit_parser.add_argument("--categories", dest="categories", action=TrackingAction, nargs="+", default=DEFAULT_CATEGORIES, choices=VALID_CATEGORIES, help="Lighthouse categories")
    audit_parser.add_argument("--budget", dest="budget", action=TrackingAction, default=None, help="Budget file (TOML) or 'cwv' preset for pass/fail evaluation")
    audit_parser.add_argument("--budget-format", dest="budget_format", action=TrackingAction, default="text", choices=("text", "json", "github"), help="Budget output format (default: text)")
    audit_parser.add_argument("--webhook", dest="webhook", action=TrackingAction, default=None, help="Webhook URL for budget notifications")
    audit_parser.add_argument("--webhook-on", dest="webhook_on", action=TrackingAction, default="always", choices=("always", "fail"), help="When to send webhook: always or fail only")
    audit_parser.add_argument(
        "--full",
        dest="full",
        action=TrackingStoreTrueAction,
        default=False,
        help="Include the raw lighthouseResult in JSON output (ignored for CSV)",
    )
    audit_parser.add_argument(
        "--stream",
        dest="stream",
        action=TrackingStoreTrueAction,
        default=False,
        help="Print results as NDJSON to stdout as they complete (skips file output)",
    )

    # --- compare ---
    compare_parser = subparsers.add_parser("compare", help="Compare two reports and highlight regressions")
    compare_parser.add_argument("before", help="Path to the 'before' report (CSV or JSON)")
    compare_parser.add_argument("after", help="Path to the 'after' report (CSV or JSON)")
    compare_parser.add_argument("--threshold", dest="threshold", type=float, default=5.0, help="Minimum %% change to highlight (default: 5)")

    # --- report ---
    report_parser = subparsers.add_parser("report", help="Generate a visual HTML report from results")
    report_parser.add_argument("input_file", help="Path to CSV or JSON results file")
    report_parser.add_argument("-o", "--output", dest="output", action=TrackingAction, default=None, help="Output HTML file path")
    report_parser.add_argument("--output-dir", dest="output_dir", action=TrackingAction, default=DEFAULT_OUTPUT_DIR, help="Directory for auto-named output files")
    report_parser.add_argument("--open", dest="open_browser", action=TrackingStoreTrueAction, default=False, help="Auto-open report in browser")

    # --- run ---
    run_parser = subparsers.add_parser("run", help="Low-level direct access with all flags")
    run_parser.add_argument("urls", nargs="*", default=[], help="URLs to analyze")
    run_parser.add_argument("-f", "--file", dest="file", action=TrackingAction, default=None, help="File with one URL per line")
    run_parser.add_argument("--sitemap", dest="sitemap", action=TrackingAction, default=None, help="URL or local path to sitemap.xml")
    run_parser.add_argument("--sitemap-limit", dest="sitemap_limit", action=TrackingAction, type=int, default=None, help="Max URLs to extract from sitemap")
    run_parser.add_argument("--sitemap-filter", dest="sitemap_filter", action=TrackingAction, default=None, help="Regex to filter sitemap URLs")
    run_parser.add_argument("-s", "--strategy", dest="strategy", action=TrackingAction, default=DEFAULT_STRATEGY, choices=VALID_STRATEGIES, help="Strategy: mobile, desktop, or both")
    run_parser.add_argument("--output-format", dest="output_format", action=TrackingAction, default=DEFAULT_OUTPUT_FORMAT, choices=VALID_OUTPUT_FORMATS, help="Output format: csv, json, or both")
    run_parser.add_argument("-o", "--output", dest="output", action=TrackingAction, default=None, help="Explicit output file path")
    run_parser.add_argument("--output-dir", dest="output_dir", action=TrackingAction, default=DEFAULT_OUTPUT_DIR, help="Directory for output files")
    run_parser.add_argument("-d", "--delay", dest="delay", action=TrackingAction, type=float, default=DEFAULT_DELAY, help="Seconds between requests")
    run_parser.add_argument("-w", "--workers", dest="workers", action=TrackingAction, type=int, default=DEFAULT_WORKERS, help="Concurrent workers")
    run_parser.add_argument("--categories", dest="categories", action=TrackingAction, nargs="+", default=DEFAULT_CATEGORIES, choices=VALID_CATEGORIES, help="Lighthouse categories")
    run_parser.add_argument("--budget", dest="budget", action=TrackingAction, default=None, help="Budget file (TOML) or 'cwv' preset for pass/fail evaluation")
    run_parser.add_argument("--budget-format", dest="budget_format", action=TrackingAction, default="text", choices=("text", "json", "github"), help="Budget output format (default: text)")
    run_parser.add_argument("--webhook", dest="webhook", action=TrackingAction, default=None, help="Webhook URL for budget notifications")
    run_parser.add_argument("--webhook-on", dest="webhook_on", action=TrackingAction, default="always", choices=("always", "fail"), help="When to send webhook: always or fail only")

    # --- pipeline ---
    pipeline_parser = subparsers.add_parser("pipeline", help="End-to-end: fetch URLs, analyze, write data files, and generate HTML report")
    pipeline_parser.add_argument("source", nargs="*", default=[], help="Sitemap URL/path (auto-detected) or plain URLs")
    pipeline_parser.add_argument("-f", "--file", dest="file", action=TrackingAction, default=None, help="File with one URL per line")
    pipeline_parser.add_argument("--sitemap", dest="sitemap", action=TrackingAction, default=None, help="Explicit sitemap URL/path (when positional args are plain URLs)")
    pipeline_parser.add_argument("--sitemap-limit", dest="sitemap_limit", action=TrackingAction, type=int, default=None, help="Max URLs to extract from sitemap")
    pipeline_parser.add_argument("--sitemap-filter", dest="sitemap_filter", action=TrackingAction, default=None, help="Regex to filter sitemap URLs")
    pipeline_parser.add_argument("-s", "--strategy", dest="strategy", action=TrackingAction, default=DEFAULT_STRATEGY, choices=VALID_STRATEGIES, help="Strategy: mobile, desktop, or both")
    pipeline_parser.add_argument("--output-format", dest="output_format", action=TrackingAction, default=DEFAULT_OUTPUT_FORMAT, choices=VALID_OUTPUT_FORMATS, help="Output format: csv, json, or both")
    pipeline_parser.add_argument("-o", "--output", dest="output", action=TrackingAction, default=None, help="Explicit output file path (overrides auto-naming)")
    pipeline_parser.add_argument("--output-dir", dest="output_dir", action=TrackingAction, default=DEFAULT_OUTPUT_DIR, help="Directory for auto-named output files")
    pipeline_parser.add_argument("-d", "--delay", dest="delay", action=TrackingAction, type=float, default=DEFAULT_DELAY, help="Seconds between API requests")
    pipeline_parser.add_argument("-w", "--workers", dest="workers", action=TrackingAction, type=int, default=DEFAULT_WORKERS, help="Concurrent workers (1 = sequential)")
    pipeline_parser.add_argument("--categories", dest="categories", action=TrackingAction, nargs="+", default=DEFAULT_CATEGORIES, choices=VALID_CATEGORIES, help="Lighthouse categories")
    pipeline_parser.add_argument("--open", dest="open_browser", action=TrackingStoreTrueAction, default=False, help="Auto-open HTML report in browser")
    pipeline_parser.add_argument("--no-report", dest="no_report", action=TrackingStoreTrueAction, default=False, help="Skip HTML report generation (data files only)")
    pipeline_parser.add_argument("--budget", dest="budget", action=TrackingAction, default=None, help="Budget file (TOML) or 'cwv' preset for pass/fail evaluation")
    pipeline_parser.add_argument("--budget-format", dest="budget_format", action=TrackingAction, default="text", choices=("text", "json", "github"), help="Budget output format (default: text)")
    pipeline_parser.add_argument("--webhook", dest="webhook", action=TrackingAction, default=None, help="Webhook URL for budget notifications")
    pipeline_parser.add_argument("--webhook-on", dest="webhook_on", action=TrackingAction, default="always", choices=("always", "fail"), help="When to send webhook: always or fail only")

    # --- budget ---
    budget_parser = subparsers.add_parser("budget", help="Evaluate existing results against a performance budget")
    budget_parser.add_argument("input_file", help="Path to CSV or JSON results file")
    budget_parser.add_argument("--budget", dest="budget", action=TrackingAction, required=True, help="Budget file (TOML) or 'cwv' preset")
    budget_parser.add_argument("--budget-format", dest="budget_format", action=TrackingAction, default="text", choices=("text", "json", "github"), help="Budget output format (default: text)")
    budget_parser.add_argument("--webhook", dest="webhook", action=TrackingAction, default=None, help="Webhook URL for budget notifications")
    budget_parser.add_argument("--webhook-on", dest="webhook_on", action=TrackingAction, default="always", choices=("always", "fail"), help="When to send webhook: always or fail only")

    return parser


# ---------------------------------------------------------------------------
# URL Handling
# ---------------------------------------------------------------------------


def validate_url(url: str) -> str | None:
    """Validate and normalize a URL. Returns the URL or None if invalid."""
    url = url.strip()
    if not url or url.startswith("#"):
        return None

    # Add scheme if missing
    if not url.startswith(("http://", "https://")):
        url = "https://" + url

    parsed = urlparse(url)
    if not parsed.netloc or "." not in parsed.netloc:
        return None
    return url


async def _fetch_sitemap_content(source: str, client: httpx.AsyncClient) -> str:
    """Fetch sitemap XML from a URL or read from a local file path."""
    if source.startswith(("http://", "https://")):
        response = await client.get(source, timeout=SITEMAP_FETCH_TIMEOUT)
        response.raise_for_status()
        return response.text
    path = Path(source)
    return path.read_text()


async def parse_sitemap_xml(
    xml_content: str,
    verbose: bool = False,
    _depth: int = 0,
    client: httpx.AsyncClient | None = None,
) -> list[str]:
    """Parse sitemap XML and return extracted URLs.

    Handles both <urlset> and <sitemapindex> root elements.
    Recursively fetches child sitemaps from index files up to MAX_SITEMAP_DEPTH.
    """
    if _depth >= MAX_SITEMAP_DEPTH:
        err_console.print(f"[yellow]Warning:[/yellow] max sitemap depth ({MAX_SITEMAP_DEPTH}) reached, stopping recursion")
        return []

    try:
        root = ET.fromstring(xml_content)
    except ET.ParseError as exc:
        err_console.print(f"[yellow]Warning:[/yellow] malformed sitemap XML: {exc}")
        return []

    # Strip namespace from tag for easier comparison
    root_tag = root.tag.split("}")[-1] if "}" in root.tag else root.tag

    urls: list[str] = []

    if root_tag == "sitemapindex":
        # Try namespaced first, then non-namespaced
        sitemap_locs = root.findall("sm:sitemap/sm:loc", SITEMAP_NS)
        if not sitemap_locs:
            sitemap_locs = root.findall("sitemap/loc")
        for loc_elem in sitemap_locs:
            child_url = loc_elem.text.strip() if loc_elem.text else ""
            if not child_url:
                continue
            if verbose:
                err_console.print(f"  Following child sitemap: {child_url}")
            try:
                child_content = await _fetch_sitemap_content(child_url, client)
                child_urls = await parse_sitemap_xml(child_content, verbose, _depth + 1, client)
                urls.extend(child_urls)
            except (httpx.HTTPError, OSError) as exc:
                err_console.print(f"[yellow]Warning:[/yellow] failed to fetch child sitemap {child_url}: {exc}")
    else:
        # Assume <urlset>
        loc_elements = root.findall("sm:url/sm:loc", SITEMAP_NS)
        if not loc_elements:
            loc_elements = root.findall("url/loc")
        for loc_elem in loc_elements:
            url_text = loc_elem.text.strip() if loc_elem.text else ""
            if url_text:
                urls.append(url_text)

    return urls


async def fetch_sitemap_urls(
    source: str,
    limit: int | None = None,
    filter_pattern: str | None = None,
    verbose: bool = False,
) -> list[str]:
    """Fetch and filter URLs from a sitemap XML source.

    Args:
        source: URL or local file path to a sitemap.xml.
        limit: Maximum number of URLs to return.
        filter_pattern: Regex pattern to filter URLs (keeps matches).
        verbose: Print progress to stderr.
    """
    async with httpx.AsyncClient() as client:
        try:
            if verbose:
                err_console.print(f"  Fetching sitemap: {source}")
            xml_content = await _fetch_sitemap_content(source, client)
            urls = await parse_sitemap_xml(xml_content, verbose, client=client)
        except (httpx.HTTPError, OSError) as exc:
            err_console.print(f"[yellow]Warning:[/yellow] failed to fetch sitemap {source}: {exc}")
            return []

    if verbose:
        err_console.print(f"  Found {len(urls)} URL(s) in sitemap")

    if filter_pattern:
        try:
            pattern = re.compile(filter_pattern)
        except re.error as exc:
            err_console.print(f"[bold red]Error:[/bold red] invalid sitemap filter regex '{filter_pattern}': {exc}")
            return []
        urls = [u for u in urls if pattern.search(u)]
        if verbose:
            err_console.print(f"  {len(urls)} URL(s) after filter '{filter_pattern}'")

    if limit is not None and limit > 0:
        urls = urls[:limit]
        if verbose:
            err_console.print(f"  Limited to {len(urls)} URL(s)")

    return urls


async def load_urls(
    url_args: list[str],
    file_path: str | None,
    allow_stdin: bool = True,
    sitemap: str | None = None,
    sitemap_limit: int | None = None,
    sitemap_filter: str | None = None,
    verbose: bool = False,
) -> list[str]:
    """Load URLs from positional args, file, stdin, and/or sitemap. Returns validated list."""
    raw_urls: list[str] = []

    if url_args:
        raw_urls.extend(url_args)
    elif file_path:
        path = Path(file_path)
        if not path.is_file():
            err_console.print(f"[bold red]Error:[/bold red] URL file not found: {file_path}")
            sys.exit(1)
        raw_urls.extend(path.read_text().splitlines())
    elif allow_stdin and not sys.stdin.isatty():
        raw_urls.extend(sys.stdin.read().splitlines())

    if sitemap:
        sitemap_urls = await fetch_sitemap_urls(
            source=sitemap,
            limit=sitemap_limit,
            filter_pattern=sitemap_filter,
            verbose=verbose,
        )
        raw_urls.extend(sitemap_urls)

    seen: set[str] = set()
    validated: list[str] = []
    for raw in raw_urls:
        cleaned = validate_url(raw)
        if cleaned:
            if cleaned not in seen:
                seen.add(cleaned)
                validated.append(cleaned)
        elif raw.strip() and not raw.strip().startswith("#"):
            err_console.print(f"[yellow]Warning:[/yellow] skipping invalid URL: {raw.strip()}")

    if not validated:
        err_console.print("[bold red]Error:[/bold red] no valid URLs provided.")
        sys.exit(1)

    return validated


def _looks_like_sitemap(source: str) -> bool:
    """Heuristic to detect whether a source string is a sitemap rather than a plain URL."""
    lower = source.lower()

    # URL/filename ending in .xml or .xml.gz
    if lower.endswith(".xml") or lower.endswith(".xml.gz"):
        return True

    # URL containing "sitemap" anywhere (e.g. /sitemap_index.xml, /sitemap)
    if "sitemap" in lower:
        return True

    # Local file: peek at content
    path = Path(source)
    if path.is_file():
        try:
            head = path.read_text(errors="ignore")[:512]
            if head.lstrip().startswith("<?xml") or "<urlset" in head or "<sitemapindex" in head:
                return True
        except OSError:
            pass

    return False


# ---------------------------------------------------------------------------
# API Client
# ---------------------------------------------------------------------------


async def fetch_pagespeed_result(
    url: str,
    strategy: str,
    api_key: str | None = None,
    categories: list[str] | None = None,
    *,
    client: httpx.AsyncClient,
) -> dict:
    """Fetch PageSpeed Insights results for a single URL + strategy.

    Retries on 429/500/503 with exponential backoff.
    """
    # httpx supports list values for repeated query params
    category_list = categories or DEFAULT_CATEGORIES
    params: dict[str, str | list[str]] = {
        "url": url,
        "strategy": strategy,
        "category": category_list,
    }
    if api_key:
        params["key"] = api_key

    last_error: Exception | None = None
    for attempt in range(MAX_RETRIES + 1):
        try:
            response = await client.get(
                PAGESPEED_API_URL,
                params=params,
                timeout=120,
            )

            if response.status_code == 200:
                data = response.json()
                if "error" in data:
                    error_message = data["error"].get("message", "Unknown API error")
                    raise PageSpeedError(
                        f"API error for {url} ({strategy}): {error_message}"
                    )
                if "lighthouseResult" not in data:
                    raise PageSpeedError(
                        f"No lighthouseResult in API response for {url} ({strategy})"
                    )
                return data

            if response.status_code in RETRYABLE_STATUS_CODES:
                retry_after = response.headers.get("Retry-After")
                if retry_after and response.status_code == 429:
                    wait_time = float(retry_after)
                else:
                    wait_time = RETRY_BASE_DELAY * (2**attempt)
                last_error = PageSpeedError(
                    f"HTTP {response.status_code} for {url} ({strategy})"
                )
                if attempt < MAX_RETRIES:
                    err_console.print(
                        f"  [yellow]⚠[/yellow] HTTP {response.status_code} — retrying in {wait_time:.1f}s "
                        f"(attempt {attempt + 1}/{MAX_RETRIES})..."
                    )
                    await asyncio.sleep(wait_time)
                    continue

            # Non-retryable error
            error_detail = ""
            try:
                error_body = response.json()
                error_detail = error_body.get("error", {}).get("message", response.text[:200])
            except (ValueError, KeyError):
                error_detail = response.text[:200]
            raise PageSpeedError(
                f"HTTP {response.status_code} for {url} ({strategy}): {error_detail}"
            )

        except (httpx.RequestError, OSError) as exc:
            last_error = exc
            if attempt < MAX_RETRIES:
                wait_time = RETRY_BASE_DELAY * (2**attempt)
                err_console.print(
                    f"  [yellow]⚠[/yellow] Request error: {exc} — retrying in {wait_time:.1f}s "
                    f"(attempt {attempt + 1}/{MAX_RETRIES})..."
                )
                await asyncio.sleep(wait_time)
                continue

    raise PageSpeedError(f"Failed after {MAX_RETRIES + 1} attempts for {url} ({strategy}): {last_error}")


# ---------------------------------------------------------------------------
# Metrics Extraction
# ---------------------------------------------------------------------------


def extract_metrics(api_response: dict, url: str, strategy: str, include_raw: bool = False) -> dict:
    """Extract lab and field metrics from a PageSpeed API response."""
    row: dict[str, object] = {
        "url": url,
        "strategy": strategy,
        "error": None,
    }

    # Performance score
    lighthouse = api_response.get("lighthouseResult", {})
    categories = lighthouse.get("categories", {})
    perf = categories.get("performance", {})
    score = perf.get("score")
    row["performance_score"] = round(score * 100) if score is not None else None

    # Additional category scores
    for cat_key in ("accessibility", "best-practices", "seo"):
        cat_data = categories.get(cat_key, {})
        cat_score = cat_data.get("score")
        column_name = cat_key.replace("-", "_") + "_score"
        row[column_name] = round(cat_score * 100) if cat_score is not None else None

    # Lab metrics
    audits = lighthouse.get("audits", {})
    for audit_id, column_name in LAB_METRICS:
        audit_data = audits.get(audit_id, {})
        value = audit_data.get("numericValue")
        if value is not None and column_name != "lab_cls":
            value = round(value)
        elif value is not None:
            value = round(value, 4)
        row[column_name] = value

    # Field metrics
    loading_exp = api_response.get("loadingExperience", {})
    field_metrics_data = loading_exp.get("metrics", {})
    for api_key, value_col, category_col in FIELD_METRICS:
        metric_data = field_metrics_data.get(api_key, {})
        percentile = metric_data.get("percentile")
        category = metric_data.get("category")
        if percentile is not None and "CLS" in api_key:
            # CLS is reported as an integer * 100 by the API
            row[value_col] = round(percentile / 100, 4)
        else:
            row[value_col] = percentile
        row[category_col] = category

    # Timestamp from lighthouse
    fetch_time = lighthouse.get("fetchTime")
    row["fetch_time"] = fetch_time

    if include_raw:
        row["_lighthouse_raw"] = api_response.get("lighthouseResult")

    return row


# ---------------------------------------------------------------------------
# Batch Processing
# ---------------------------------------------------------------------------


async def process_urls(
    urls: list[str],
    api_key: str | None,
    strategies: list[str],
    categories: list[str],
    delay: float,
    workers: int,
    verbose: bool = False,
    full: bool = False,
    on_result: Callable[[dict], None] | None = None,
) -> pd.DataFrame:
    """Process multiple URLs concurrently and return a DataFrame of results."""
    results: list[dict] = []
    task_list = [(url, strategy) for url in urls for strategy in strategies]
    total_tasks = len(task_list)
    semaphore = asyncio.Semaphore(1)  # rate limiter
    last_request_time = [0.0]  # mutable for closure

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

    async def process_single(url: str, strategy: str) -> dict:
        # Rate limiting
        async with semaphore:
            now = time.monotonic()
            elapsed = now - last_request_time[0]
            if elapsed < delay:
                await asyncio.sleep(delay - elapsed)
            last_request_time[0] = time.monotonic()

        short_url = url if len(url) <= 50 else url[:47] + "..."
        progress.update(prog_task, description=f"[cyan]{short_url}[/cyan] ({strategy})")

        if verbose:
            err_console.print(f"  [dim]Fetching[/dim] [cyan]{url}[/cyan] ({strategy})...")

        try:
            response = await fetch_pagespeed_result(url, strategy, api_key, categories, client=client)
            metrics = extract_metrics(response, url, strategy, include_raw=full)
        except PageSpeedError as exc:
            metrics = {
                "url": url,
                "strategy": strategy,
                "error": str(exc),
            }
            err_console.print(f"  [bold red]Error:[/bold red] {exc}")

        progress.advance(prog_task)

        if on_result:
            on_result(metrics)

        return metrics

    async with httpx.AsyncClient() as client:
        with progress:
            effective_workers = min(workers, total_tasks)
            if effective_workers <= 1:
                for url, strategy in task_list:
                    results.append(await process_single(url, strategy))
            else:
                results = list(await asyncio.gather(*[
                    process_single(url, strategy)
                    for url, strategy in task_list
                ]))

    return pd.DataFrame(results)


# ---------------------------------------------------------------------------
# Output Formats
# ---------------------------------------------------------------------------


def generate_output_path(output_dir: str, strategy: str, extension: str) -> Path:
    """Generate a timestamped output file path."""
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    dir_path = Path(output_dir)
    dir_path.mkdir(parents=True, exist_ok=True)
    return dir_path / f"{timestamp}-{strategy}.{extension}"


def _write_data_files(
    dataframe: pd.DataFrame,
    output_format: str,
    output_dir: str,
    explicit_output: str | None,
    strategy_label: str,
) -> list[str]:
    """Write CSV and/or JSON data files based on output_format. Returns list of written paths."""
    written_files: list[str] = []

    if output_format in ("csv", "both"):
        if explicit_output:
            csv_path = Path(explicit_output).with_suffix(".csv")
        else:
            csv_path = generate_output_path(output_dir, strategy_label, "csv")
        written_files.append(output_csv(dataframe, csv_path))

    if output_format in ("json", "both"):
        if explicit_output:
            json_path = Path(explicit_output).with_suffix(".json")
        else:
            json_path = generate_output_path(output_dir, strategy_label, "json")
        written_files.append(output_json(dataframe, json_path))

    err_console.print("")
    for filepath in written_files:
        err_console.print(f"  [green]✓[/green] [cyan]{filepath}[/cyan]")

    # Write errors.csv if there are any failed URLs
    if "error" in dataframe.columns:
        error_rows = dataframe[dataframe["error"].notna()]
        if not error_rows.empty:
            errors_path = generate_output_path(output_dir, "errors", "csv")
            output_csv(error_rows[["url", "strategy", "error"]], errors_path)
            err_console.print(f"  [yellow]⚠[/yellow]  [cyan]{errors_path}[/cyan] ({len(error_rows)} failed URL{'s' if len(error_rows) != 1 else ''})")

    return written_files


def _print_audit_summary(dataframe: pd.DataFrame) -> None:
    """Print average/min/max scores and error count to stderr."""
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

    err_console.print(Panel(t, title="Summary", border_style="blue"))


def evaluate_budget(dataframe: pd.DataFrame, budget: dict) -> dict:
    """Evaluate a DataFrame of results against a performance budget.

    Returns a verdict dict with per-URL results and overall pass/fail status.
    """
    thresholds = budget.get("thresholds", {})
    budget_name = budget.get("meta", {}).get("name", "Performance budget")

    # Filter to non-error rows
    if "error" in dataframe.columns:
        valid_rows = dataframe[dataframe["error"].isna() | (dataframe["error"] == "")]
        error_count = len(dataframe) - len(valid_rows)
    else:
        valid_rows = dataframe
        error_count = 0

    if len(valid_rows) == 0:
        return {
            "budget_name": budget_name,
            "verdict": "error",
            "passed": 0,
            "failed": 0,
            "total": 0,
            "errors_skipped": error_count,
            "results": [],
        }

    if not thresholds:
        err_console.print("[yellow]Warning:[/yellow] budget has no thresholds defined — all URLs pass by default")

    results = []
    passed_count = 0
    failed_count = 0

    for _, row in valid_rows.iterrows():
        url = row.get("url", "")
        strategy = row.get("strategy", "")
        violations = []

        for budget_key, (column_name, operator) in BUDGET_METRIC_MAP.items():
            if budget_key not in thresholds:
                continue
            if column_name not in dataframe.columns:
                continue
            actual = row.get(column_name)
            if actual is None or (isinstance(actual, float) and math.isnan(actual)):
                continue

            threshold_value = thresholds[budget_key]
            if operator == ">=" and actual < threshold_value:
                violations.append({
                    "metric": column_name,
                    "actual": actual,
                    "threshold": threshold_value,
                    "operator": operator,
                })
            elif operator == "<=" and actual > threshold_value:
                violations.append({
                    "metric": column_name,
                    "actual": actual,
                    "threshold": threshold_value,
                    "operator": operator,
                })

        row_verdict = "fail" if violations else "pass"
        if row_verdict == "pass":
            passed_count += 1
        else:
            failed_count += 1

        results.append({
            "url": url,
            "strategy": strategy,
            "verdict": row_verdict,
            "violations": violations,
        })

    overall_verdict = "fail" if failed_count > 0 else "pass"
    total = passed_count + failed_count

    return {
        "budget_name": budget_name,
        "verdict": overall_verdict,
        "passed": passed_count,
        "failed": failed_count,
        "total": total,
        "errors_skipped": error_count,
        "results": results,
    }


def format_budget_text(verdict: dict) -> str:
    """Format budget verdict as human-readable text for terminal output."""
    budget_name = verdict["budget_name"]
    overall = verdict["verdict"].upper()
    passed = verdict["passed"]
    failed = verdict["failed"]
    total = verdict["total"]
    errors_skipped = verdict["errors_skipped"]

    lines = [
        f"Budget: {budget_name}",
        f"Result: {overall} ({passed} passed, {failed} failed, {total} total, {errors_skipped} skipped)",
        "",
    ]

    for result in verdict["results"]:
        url = result["url"]
        strategy = result["strategy"]
        row_verdict = result["verdict"].upper()
        lines.append(f"{row_verdict}  {url} ({strategy})")
        for violation in result["violations"]:
            metric = violation["metric"]
            actual = violation["actual"]
            threshold = violation["threshold"]
            operator = violation["operator"]
            lines.append(f"      {metric}: {actual} (threshold: {operator} {threshold})")

    return "\n".join(lines)


def format_budget_json(verdict: dict) -> str:
    """Format budget verdict as JSON."""
    return json.dumps(verdict, indent=2, default=str)


def format_budget_github(verdict: dict) -> str:
    """Format budget verdict as GitHub Actions annotations."""
    lines = []
    for result in verdict["results"]:
        if result["verdict"] == "fail":
            url = result["url"]
            strategy = result["strategy"]
            for violation in result["violations"]:
                metric = violation["metric"]
                actual = violation["actual"]
                operator = violation["operator"]
                threshold = violation["threshold"]
                lines.append(
                    f"::error::Budget FAIL: {url} ({strategy}) "
                    f"— {metric}={actual} ({operator} {threshold})"
                )
    if not lines and verdict["verdict"] == "pass":
        lines.append(f"::notice::Budget PASS: {verdict['budget_name']}")
    return "\n".join(lines)


async def send_budget_webhook(webhook_url: str, verdict: dict) -> None:
    """POST budget verdict to a webhook URL. Failures are warnings only."""
    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(webhook_url, json=verdict, timeout=30)
            response.raise_for_status()
    except (httpx.HTTPError, OSError) as exc:
        err_console.print(f"[yellow]Warning:[/yellow] webhook delivery failed: {exc}")


async def _apply_budget(dataframe: pd.DataFrame, args: argparse.Namespace) -> int:
    """Orchestrate budget evaluation when --budget is set. Returns exit code."""
    budget = load_budget(args.budget)
    verdict = evaluate_budget(dataframe, budget)

    if verdict["verdict"] == "error":
        err_console.print("[bold red]Error:[/bold red] all URLs errored — cannot evaluate budget")
        return 1

    # Pick output format: explicit flag > GitHub Actions auto-detect > text
    cli_explicit = set(getattr(args, "_explicit_args", []))
    budget_format = getattr(args, "budget_format", "text")
    if "budget_format" not in cli_explicit and os.environ.get("GITHUB_ACTIONS"):
        budget_format = "github"

    formatters = {
        "text": format_budget_text,
        "json": format_budget_json,
        "github": format_budget_github,
    }
    formatter = formatters.get(budget_format, format_budget_text)
    err_console.print(formatter(verdict))

    # Webhook
    webhook_url = getattr(args, "webhook", None)
    if webhook_url:
        webhook_on = getattr(args, "webhook_on", "always")
        if webhook_on == "always" or (webhook_on == "fail" and verdict["verdict"] == "fail"):
            await send_budget_webhook(webhook_url, verdict)

    if verdict["verdict"] == "fail":
        return BUDGET_EXIT_CODE
    return 0


def _row_to_ndjson(row: dict) -> str:
    """Serialize a result dict to a single NDJSON line, replacing NaN with null."""
    cleaned = {}
    for key, value in row.items():
        try:
            cleaned[key] = None if pd.isna(value) else value
        except (TypeError, ValueError):
            cleaned[key] = value
    return json.dumps(cleaned, default=str)


def output_csv(dataframe: pd.DataFrame, output_path: Path) -> str:
    """Write DataFrame to CSV. Returns the file path."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    dataframe = dataframe.drop(columns=["_lighthouse_raw"], errors="ignore")
    dataframe.to_csv(output_path, index=False)
    return str(output_path)


def output_json(dataframe: pd.DataFrame, output_path: Path) -> str:
    """Write DataFrame to structured JSON with metadata. Returns the file path."""
    output_path.parent.mkdir(parents=True, exist_ok=True)

    strategies_present = dataframe["strategy"].unique().tolist() if "strategy" in dataframe.columns else []

    results = []
    for _, row in dataframe.iterrows():
        error_val = row.get("error")
        record = {
            "url": row.get("url"),
            "strategy": row.get("strategy"),
            "error": None if pd.isna(error_val) else error_val,
        }

        # Performance score
        perf_score = row.get("performance_score")
        record["performance_score"] = None if pd.isna(perf_score) else perf_score

        # Additional scores
        for score_key in ("accessibility_score", "best_practices_score", "seo_score"):
            if score_key in row and pd.notna(row[score_key]):
                record[score_key] = row[score_key]

        # Lab metrics
        lab = {}
        for _, col_name in LAB_METRICS:
            if col_name in row and pd.notna(row[col_name]):
                lab[col_name] = row[col_name]
        if lab:
            record["lab_metrics"] = lab

        # Field metrics
        field = {}
        for _, value_col, category_col in FIELD_METRICS:
            if value_col in row and pd.notna(row[value_col]):
                field[value_col] = row[value_col]
            if category_col in row and pd.notna(row[category_col]):
                field[category_col] = row[category_col]
        if field:
            record["field_metrics"] = field

        fetch_time = row.get("fetch_time")
        record["fetch_time"] = None if pd.isna(fetch_time) else fetch_time

        if "_lighthouse_raw" in row.index and pd.notna(row["_lighthouse_raw"]):
            record["lighthouseResult"] = row["_lighthouse_raw"]

        results.append(record)

    output_data = {
        "metadata": {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "total_urls": len(dataframe["url"].unique()) if "url" in dataframe.columns else 0,
            "strategies": strategies_present,
            "tool_version": __version__,
        },
        "results": results,
    }

    with open(output_path, "w") as fh:
        json.dump(output_data, fh, indent=2, default=str)

    return str(output_path)


def format_terminal_table(metrics: dict | list[dict]) -> Group:
    """Format metrics as rich Panels (one per result), grouped for console output."""
    if isinstance(metrics, dict):
        metrics_list = [metrics]
    else:
        metrics_list = metrics

    panels = []
    for row_data in metrics_list:
        url = row_data.get("url", "?")
        strategy = row_data.get("strategy", "?")
        error = row_data.get("error")

        panel_title = f"[bold cyan]{url}[/bold cyan]  [dim]{strategy}[/dim]"

        if error:
            error_text = Text(str(error), style="bold red")
            panels.append(Panel(error_text, title=panel_title, border_style="red"))
            continue

        score = row_data.get("performance_score")
        border_color = _score_color(score)

        t = Table(box=box.SIMPLE, show_header=False, padding=(0, 1), expand=False)
        t.add_column("Label", style="dim", no_wrap=True)
        t.add_column("Value")

        # Performance score
        if score is not None:
            t.add_row("Performance Score", _score_text(score))

        # Additional category scores
        for label, key in [
            ("Accessibility", "accessibility_score"),
            ("Best Practices", "best_practices_score"),
            ("SEO", "seo_score"),
        ]:
            val = row_data.get(key)
            if val is not None:
                t.add_row(label, _score_text(val))

        # Lab metrics section header
        t.add_row("[bold]Lab Data[/bold]", "")
        lab_display = [
            ("First Contentful Paint", "lab_fcp_ms", "ms"),
            ("Largest Contentful Paint", "lab_lcp_ms", "ms"),
            ("Cumulative Layout Shift", "lab_cls", ""),
            ("Speed Index", "lab_speed_index_ms", "ms"),
            ("Total Blocking Time", "lab_tbt_ms", "ms"),
            ("Time to Interactive", "lab_tti_ms", "ms"),
        ]
        for label, key, unit in lab_display:
            val = row_data.get(key)
            if val is not None:
                suffix = f" {unit}" if unit else ""
                t.add_row(label, f"{val}{suffix}")

        # Field metrics section header + rows
        has_field = any(row_data.get(vc) is not None for _, vc, _ in FIELD_METRICS)
        if has_field:
            t.add_row("[bold]Field Data (CrUX)[/bold]", "")
            field_display = [
                ("FCP", "field_fcp_ms", "field_fcp_category", "ms"),
                ("LCP", "field_lcp_ms", "field_lcp_category", "ms"),
                ("CLS", "field_cls", "field_cls_category", ""),
                ("INP", "field_inp_ms", "field_inp_category", "ms"),
                ("FID", "field_fid_ms", "field_fid_category", "ms"),
                ("TTFB", "field_ttfb_ms", "field_ttfb_category", "ms"),
            ]
            for label, val_key, cat_key, unit in field_display:
                val = row_data.get(val_key)
                cat = row_data.get(cat_key)
                if val is not None:
                    suffix = f" {unit}" if unit else ""
                    cat_style = _field_cat_color(cat)
                    cat_str = Text(f" [{cat}]", style=cat_style) if cat else Text("")
                    value_text = Text(f"{val}{suffix}") + cat_str
                    t.add_row(label, value_text)

        panels.append(Panel(t, title=panel_title, border_style=border_color))

    return Group(*panels)


# ---------------------------------------------------------------------------
# Report Loading (for compare / report subcommands)
# ---------------------------------------------------------------------------


def load_report(file_path: str) -> pd.DataFrame:
    """Load a report from CSV or JSON into a DataFrame."""
    path = Path(file_path)
    if not path.is_file():
        err_console.print(f"[bold red]Error:[/bold red] report file not found: {file_path}")
        sys.exit(1)

    suffix = path.suffix.lower()
    if suffix == ".csv":
        return pd.read_csv(path)
    elif suffix == ".json":
        with open(path) as fh:
            data = json.load(fh)
        if "results" in data:
            # Structured JSON format — flatten lab_metrics and field_metrics
            rows = []
            for result in data["results"]:
                flat_row = {
                    "url": result.get("url"),
                    "strategy": result.get("strategy"),
                    "error": result.get("error"),
                    "performance_score": result.get("performance_score"),
                    "fetch_time": result.get("fetch_time"),
                }
                for key in ("accessibility_score", "best_practices_score", "seo_score"):
                    if key in result:
                        flat_row[key] = result[key]
                for key, value in result.get("lab_metrics", {}).items():
                    flat_row[key] = value
                for key, value in result.get("field_metrics", {}).items():
                    flat_row[key] = value
                rows.append(flat_row)
            return pd.DataFrame(rows)
        else:
            return pd.DataFrame(data)
    else:
        err_console.print(f"[bold red]Error:[/bold red] unsupported file format '{suffix}'. Use .csv or .json.")
        sys.exit(1)


# ---------------------------------------------------------------------------
# Subcommand: quick-check
# ---------------------------------------------------------------------------


async def cmd_quick_check(args: argparse.Namespace) -> None:
    """Run a quick single-URL spot check and print results to stdout."""
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


# ---------------------------------------------------------------------------
# Subcommand: audit
# ---------------------------------------------------------------------------


async def cmd_audit(args: argparse.Namespace) -> None:
    """Run a full batch analysis and write report files."""
    urls = await load_urls(
        getattr(args, "urls", []),
        getattr(args, "file", None),
        sitemap=getattr(args, "sitemap", None),
        sitemap_limit=getattr(args, "sitemap_limit", None),
        sitemap_filter=getattr(args, "sitemap_filter", None),
        verbose=getattr(args, "verbose", False),
    )
    strategies = [args.strategy] if args.strategy != "both" else ["mobile", "desktop"]
    categories = getattr(args, "categories", DEFAULT_CATEGORIES)
    full = getattr(args, "full", False)
    stream = getattr(args, "stream", False)

    on_result: Callable[[dict], None] | None = None
    if stream:
        def on_result(row: dict) -> None:
            out_console.print(_row_to_ndjson(row))

    err_console.print(
        f"Auditing [bold]{len(urls)}[/bold] URL(s) · strategy: [cyan]{args.strategy}[/cyan]"
    )
    dataframe = await process_urls(
        urls=urls,
        api_key=args.api_key,
        strategies=strategies,
        categories=categories,
        delay=args.delay,
        workers=args.workers,
        verbose=args.verbose,
        full=full,
        on_result=on_result,
    )

    strategy_label = args.strategy if args.strategy != "both" else "both"
    if full:
        strategy_label = f"{strategy_label}-full"
    output_format = getattr(args, "output_format", DEFAULT_OUTPUT_FORMAT)
    output_dir = getattr(args, "output_dir", DEFAULT_OUTPUT_DIR)
    explicit_output = getattr(args, "output", None)

    if not stream:
        _write_data_files(dataframe, output_format, output_dir, explicit_output, strategy_label)
        _print_audit_summary(dataframe)

    if getattr(args, "budget", None):
        exit_code = await _apply_budget(dataframe, args)
        sys.exit(exit_code)


# ---------------------------------------------------------------------------
# Subcommand: compare
# ---------------------------------------------------------------------------


def cmd_compare(args: argparse.Namespace) -> None:
    """Compare two reports and highlight regressions/improvements."""
    before_df = load_report(args.before)
    after_df = load_report(args.after)

    threshold = args.threshold

    # Merge on url + strategy
    merge_keys = ["url", "strategy"]
    merged = pd.merge(
        before_df,
        after_df,
        on=merge_keys,
        suffixes=("_before", "_after"),
        how="outer",
        indicator=True,
    )

    score_columns = ["performance_score"]
    for extra in ("accessibility_score", "best_practices_score", "seo_score"):
        if f"{extra}_before" in merged.columns or f"{extra}_after" in merged.columns:
            score_columns.append(extra)

    print(f"\n{'URL':<50} {'Strategy':<10}", end="")
    for col in score_columns:
        label = col.replace("_score", "").replace("_", " ").title()
        print(f" {'Before':>8} {'After':>8} {'Delta':>8}", end="")
    print()
    print("-" * (60 + len(score_columns) * 26))

    for _, row in merged.iterrows():
        url = row["url"]
        strategy = row.get("strategy", "?")
        # Truncate URL for display
        display_url = (url[:47] + "...") if len(str(url)) > 50 else url
        print(f"{display_url:<50} {strategy:<10}", end="")

        for col in score_columns:
            before_col = f"{col}_before"
            after_col = f"{col}_after"
            before_val = row.get(before_col)
            after_val = row.get(after_col)

            if pd.isna(before_val) and pd.isna(after_val):
                print(f" {'N/A':>8} {'N/A':>8} {'':>8}", end="")
            elif pd.isna(before_val):
                print(f" {'N/A':>8} {after_val:>8.0f} {'NEW':>8}", end="")
            elif pd.isna(after_val):
                print(f" {before_val:>8.0f} {'N/A':>8} {'GONE':>8}", end="")
            else:
                delta = after_val - before_val
                delta_str = f"{delta:+.0f}"
                if abs(delta) >= threshold:
                    if delta < 0:
                        delta_str = f"{delta_str} !!"  # regression
                    else:
                        delta_str = f"{delta_str} ++"  # improvement
                print(f" {before_val:>8.0f} {after_val:>8.0f} {delta_str:>8}", end="")

        print()

    # Summary
    if "performance_score_before" in merged.columns and "performance_score_after" in merged.columns:
        before_scores = merged["performance_score_before"].dropna()
        after_scores = merged["performance_score_after"].dropna()
        if len(before_scores) > 0 and len(after_scores) > 0:
            print(f"\nSummary:")
            print(f"  Before avg: {before_scores.mean():.1f}")
            print(f"  After avg:  {after_scores.mean():.1f}")
            delta_avg = after_scores.mean() - before_scores.mean()
            direction = "improvement" if delta_avg > 0 else "regression" if delta_avg < 0 else "no change"
            print(f"  Change:     {delta_avg:+.1f} ({direction})")

    regressions = 0
    improvements = 0
    if "performance_score_before" in merged.columns and "performance_score_after" in merged.columns:
        for _, row in merged.iterrows():
            before_val = row.get("performance_score_before")
            after_val = row.get("performance_score_after")
            if pd.notna(before_val) and pd.notna(after_val):
                delta = after_val - before_val
                if delta <= -threshold:
                    regressions += 1
                elif delta >= threshold:
                    improvements += 1

    print(f"  Regressions (>= {threshold}% drop): {regressions}")
    print(f"  Improvements (>= {threshold}% gain): {improvements}")
    print(f"  Threshold: {threshold}%")
    print(f"\n  Legend: !! = regression, ++ = improvement")


# ---------------------------------------------------------------------------
# Subcommand: report
# ---------------------------------------------------------------------------


def generate_html_report(dataframe: pd.DataFrame) -> str:
    """Generate a self-contained HTML dashboard from results data."""
    generated_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    total_urls = len(dataframe["url"].unique()) if "url" in dataframe.columns else 0
    strategies_present = dataframe["strategy"].unique().tolist() if "strategy" in dataframe.columns else []
    has_both_strategies = len(strategies_present) > 1

    # Compute summary stats
    scores = dataframe["performance_score"].dropna() if "performance_score" in dataframe.columns else pd.Series(dtype=float)
    avg_score = scores.mean() if len(scores) > 0 else 0
    best_score = scores.max() if len(scores) > 0 else 0
    worst_score = scores.min() if len(scores) > 0 else 0
    error_count = len(dataframe[dataframe["error"].notna()]) if "error" in dataframe.columns else 0

    def score_color(score):
        if pd.isna(score):
            return "#999"
        if score >= 90:
            return "#0cce6b"
        if score >= 50:
            return "#ffa400"
        return "#ff4e42"

    def score_class(score):
        if pd.isna(score):
            return "na"
        if score >= 90:
            return "good"
        if score >= 50:
            return "needs-work"
        return "poor"

    def cwv_status(value, metric_key):
        if pd.isna(value) or value is None or metric_key not in CWV_THRESHOLDS:
            return "na", "N/A"
        thresholds = CWV_THRESHOLDS[metric_key]
        if value <= thresholds["good"]:
            return "good", "Pass"
        if value <= thresholds["poor"]:
            return "needs-work", "Needs Work"
        return "poor", "Fail"

    # Build table rows
    table_rows = []
    for _, row in dataframe.iterrows():
        url = row.get("url", "")
        strategy = row.get("strategy", "")
        perf_score = row.get("performance_score")
        error = row.get("error")

        if pd.notna(error) and error:
            table_rows.append(f"""
            <tr>
                <td class="url-cell" title="{url}">{url}</td>
                <td>{strategy}</td>
                <td colspan="8" class="error-cell">Error: {error}</td>
            </tr>""")
            continue

        perf_class = score_class(perf_score)
        perf_display = f"{perf_score:.0f}" if pd.notna(perf_score) else "N/A"

        # CWV cells
        cwv_cells = ""
        for metric_key, display_name in [("lab_lcp_ms", "LCP"), ("lab_cls", "CLS"), ("lab_tbt_ms", "TBT")]:
            val = row.get(metric_key)
            status_class, status_label = cwv_status(val, metric_key)
            if pd.notna(val) and val is not None:
                thresholds = CWV_THRESHOLDS.get(metric_key, {})
                unit = thresholds.get("unit", "")
                if metric_key == "lab_cls":
                    val_display = f"{val:.3f}"
                else:
                    val_display = f"{val:,.0f}{unit}"
                cwv_cells += f'<td class="cwv-{status_class}" title="{status_label}">{val_display}</td>'
            else:
                cwv_cells += '<td class="cwv-na">N/A</td>'

        # Lab metrics for display
        fcp = row.get("lab_fcp_ms")
        si = row.get("lab_speed_index_ms")
        tti = row.get("lab_tti_ms")
        fcp_display = f"{fcp:,.0f}ms" if pd.notna(fcp) else "N/A"
        si_display = f"{si:,.0f}ms" if pd.notna(si) else "N/A"
        tti_display = f"{tti:,.0f}ms" if pd.notna(tti) else "N/A"

        table_rows.append(f"""
            <tr>
                <td class="url-cell" title="{url}">{url}</td>
                <td>{strategy}</td>
                <td class="score-cell {perf_class}">{perf_display}</td>
                {cwv_cells}
                <td>{fcp_display}</td>
                <td>{si_display}</td>
                <td>{tti_display}</td>
            </tr>""")

    table_rows_html = "\n".join(table_rows)

    # Build bar chart
    bar_chart_items = []
    for _, row in dataframe.iterrows():
        score = row.get("performance_score")
        if pd.isna(score):
            continue
        url = row.get("url", "")
        strategy = row.get("strategy", "")
        color = score_color(score)
        label = f"{url} ({strategy})" if has_both_strategies else url
        # Truncate label for display
        display_label = (label[:60] + "...") if len(label) > 63 else label
        bar_chart_items.append(f"""
            <div class="bar-row">
                <div class="bar-label" title="{label}">{display_label}</div>
                <div class="bar-track">
                    <div class="bar-fill" style="width: {score}%; background: {color};">{score:.0f}</div>
                </div>
            </div>""")
    bar_chart_html = "\n".join(bar_chart_items)

    # Field data section
    field_section = ""
    has_field_data = any(
        dataframe[vc].notna().any()
        for _, vc, _ in FIELD_METRICS
        if vc in dataframe.columns
    )
    if has_field_data:
        field_rows = []
        for _, row in dataframe.iterrows():
            url = row.get("url", "")
            strategy = row.get("strategy", "")
            cells = ""
            for _, val_col, cat_col in FIELD_METRICS:
                val = row.get(val_col)
                cat = row.get(cat_col)
                if pd.notna(val) and val is not None:
                    cat_class = str(cat).lower().replace("_", "-") if pd.notna(cat) else "na"
                    cat_display = str(cat) if pd.notna(cat) else ""
                    if "cls" in val_col:
                        cells += f'<td class="field-{cat_class}">{val:.3f} <small>{cat_display}</small></td>'
                    else:
                        cells += f'<td class="field-{cat_class}">{val:,.0f}ms <small>{cat_display}</small></td>'
                else:
                    cells += '<td class="field-na">N/A</td>'
            field_rows.append(f"""
                <tr>
                    <td class="url-cell" title="{url}">{url}</td>
                    <td>{strategy}</td>
                    {cells}
                </tr>""")
        field_rows_html = "\n".join(field_rows)
        field_section = f"""
        <h2>Field Data (CrUX)</h2>
        <table class="data-table sortable" id="field-table">
            <thead>
                <tr>
                    <th onclick="sortTable('field-table', 0)">URL</th>
                    <th onclick="sortTable('field-table', 1)">Strategy</th>
                    <th onclick="sortTable('field-table', 2)">FCP</th>
                    <th onclick="sortTable('field-table', 3)">LCP</th>
                    <th onclick="sortTable('field-table', 4)">CLS</th>
                    <th onclick="sortTable('field-table', 5)">INP</th>
                    <th onclick="sortTable('field-table', 6)">FID</th>
                    <th onclick="sortTable('field-table', 7)">TTFB</th>
                </tr>
            </thead>
            <tbody>
                {field_rows_html}
            </tbody>
        </table>"""

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>PageSpeed Insights Report - {generated_at}</title>
<style>
    * {{ margin: 0; padding: 0; box-sizing: border-box; }}
    body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; background: #f5f5f5; color: #333; padding: 20px; max-width: 1400px; margin: 0 auto; }}
    h1 {{ font-size: 1.5rem; margin-bottom: 5px; }}
    h2 {{ font-size: 1.2rem; margin: 30px 0 15px; color: #555; }}
    .meta {{ color: #888; font-size: 0.85rem; margin-bottom: 25px; }}
    .cards {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); gap: 15px; margin-bottom: 30px; }}
    .card {{ background: #fff; border-radius: 8px; padding: 20px; box-shadow: 0 1px 3px rgba(0,0,0,0.1); text-align: center; }}
    .card .value {{ font-size: 2rem; font-weight: 700; }}
    .card .label {{ font-size: 0.8rem; color: #888; margin-top: 5px; }}
    .card .value.good {{ color: #0cce6b; }}
    .card .value.needs-work {{ color: #ffa400; }}
    .card .value.poor {{ color: #ff4e42; }}
    .data-table {{ width: 100%; border-collapse: collapse; background: #fff; border-radius: 8px; overflow: hidden; box-shadow: 0 1px 3px rgba(0,0,0,0.1); margin-bottom: 20px; }}
    .data-table th {{ background: #f8f9fa; padding: 10px 12px; text-align: left; font-size: 0.8rem; text-transform: uppercase; color: #666; cursor: pointer; user-select: none; white-space: nowrap; }}
    .data-table th:hover {{ background: #e9ecef; }}
    .data-table td {{ padding: 10px 12px; border-top: 1px solid #eee; font-size: 0.9rem; }}
    .url-cell {{ max-width: 350px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }}
    .score-cell {{ font-weight: 700; text-align: center; min-width: 60px; }}
    .score-cell.good {{ color: #0cce6b; }}
    .score-cell.needs-work {{ color: #ffa400; }}
    .score-cell.poor {{ color: #ff4e42; }}
    .score-cell.na {{ color: #999; }}
    .cwv-good {{ color: #0cce6b; font-weight: 600; }}
    .cwv-needs-work {{ color: #ffa400; font-weight: 600; }}
    .cwv-poor {{ color: #ff4e42; font-weight: 600; }}
    .cwv-na {{ color: #999; }}
    .field-fast {{ color: #0cce6b; }}
    .field-average {{ color: #ffa400; }}
    .field-slow {{ color: #ff4e42; }}
    .field-na {{ color: #999; }}
    .error-cell {{ color: #ff4e42; font-style: italic; }}
    .bar-row {{ display: flex; align-items: center; margin-bottom: 8px; }}
    .bar-label {{ width: 300px; min-width: 200px; font-size: 0.8rem; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; padding-right: 10px; }}
    .bar-track {{ flex: 1; background: #eee; border-radius: 4px; height: 24px; position: relative; }}
    .bar-fill {{ height: 100%; border-radius: 4px; color: #fff; font-size: 0.75rem; font-weight: 700; display: flex; align-items: center; justify-content: flex-end; padding-right: 8px; min-width: 30px; transition: width 0.3s; }}
    .chart-container {{ background: #fff; border-radius: 8px; padding: 20px; box-shadow: 0 1px 3px rgba(0,0,0,0.1); margin-bottom: 20px; }}
    .legend {{ display: flex; gap: 20px; margin-top: 15px; font-size: 0.8rem; }}
    .legend-item {{ display: flex; align-items: center; gap: 5px; }}
    .legend-dot {{ width: 12px; height: 12px; border-radius: 50%; }}
    footer {{ margin-top: 40px; padding-top: 15px; border-top: 1px solid #ddd; color: #999; font-size: 0.75rem; text-align: center; }}
</style>
</head>
<body>
<h1>PageSpeed Insights Report</h1>
<p class="meta">Generated: {generated_at} | Tool v{__version__}</p>

<div class="cards">
    <div class="card"><div class="value">{total_urls}</div><div class="label">URLs Analyzed</div></div>
    <div class="card"><div class="value {score_class(avg_score)}">{avg_score:.0f}</div><div class="label">Average Score</div></div>
    <div class="card"><div class="value {score_class(best_score)}">{best_score:.0f}</div><div class="label">Best Score</div></div>
    <div class="card"><div class="value {score_class(worst_score)}">{worst_score:.0f}</div><div class="label">Worst Score</div></div>
    {"<div class='card'><div class='value poor'>" + str(error_count) + "</div><div class='label'>Errors</div></div>" if error_count > 0 else ""}
</div>

<h2>Performance Scores</h2>
<div class="chart-container">
    {bar_chart_html}
    <div class="legend">
        <div class="legend-item"><div class="legend-dot" style="background: #0cce6b;"></div> Good (90-100)</div>
        <div class="legend-item"><div class="legend-dot" style="background: #ffa400;"></div> Needs Work (50-89)</div>
        <div class="legend-item"><div class="legend-dot" style="background: #ff4e42;"></div> Poor (0-49)</div>
    </div>
</div>

<h2>Detailed Results</h2>
<table class="data-table sortable" id="results-table">
    <thead>
        <tr>
            <th onclick="sortTable('results-table', 0)">URL</th>
            <th onclick="sortTable('results-table', 1)">Strategy</th>
            <th onclick="sortTable('results-table', 2)">Score</th>
            <th onclick="sortTable('results-table', 3)">LCP</th>
            <th onclick="sortTable('results-table', 4)">CLS</th>
            <th onclick="sortTable('results-table', 5)">TBT</th>
            <th onclick="sortTable('results-table', 6)">FCP</th>
            <th onclick="sortTable('results-table', 7)">SI</th>
            <th onclick="sortTable('results-table', 8)">TTI</th>
        </tr>
    </thead>
    <tbody>
        {table_rows_html}
    </tbody>
</table>

{field_section}

<footer>
    Generated by PageSpeed Insights Batch Analysis Tool v{__version__}
</footer>

<script>
function sortTable(tableId, colIdx) {{
    const table = document.getElementById(tableId);
    const tbody = table.querySelector('tbody');
    const rows = Array.from(tbody.querySelectorAll('tr'));
    const header = table.querySelectorAll('th')[colIdx];
    const ascending = header.dataset.sort !== 'asc';
    header.dataset.sort = ascending ? 'asc' : 'desc';

    rows.sort((a, b) => {{
        const aText = a.cells[colIdx] ? a.cells[colIdx].textContent.trim() : '';
        const bText = b.cells[colIdx] ? b.cells[colIdx].textContent.trim() : '';
        const aNum = parseFloat(aText.replace(/[^\\d.-]/g, ''));
        const bNum = parseFloat(bText.replace(/[^\\d.-]/g, ''));
        if (!isNaN(aNum) && !isNaN(bNum)) {{
            return ascending ? aNum - bNum : bNum - aNum;
        }}
        return ascending ? aText.localeCompare(bText) : bText.localeCompare(aText);
    }});

    rows.forEach(row => tbody.appendChild(row));
}}
</script>
</body>
</html>"""
    return html


def cmd_report(args: argparse.Namespace) -> None:
    """Generate a visual HTML report from a results file."""
    dataframe = load_report(args.input_file)

    explicit_output = getattr(args, "output", None)
    output_dir = getattr(args, "output_dir", DEFAULT_OUTPUT_DIR)

    if explicit_output:
        html_path = Path(explicit_output)
    else:
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        dir_path = Path(output_dir)
        dir_path.mkdir(parents=True, exist_ok=True)
        html_path = dir_path / f"{timestamp}-report.html"

    html_content = generate_html_report(dataframe)
    html_path.parent.mkdir(parents=True, exist_ok=True)
    html_path.write_text(html_content)
    err_console.print(f"  [green]✓[/green] HTML report: [cyan]{html_path}[/cyan]")

    if getattr(args, "open_browser", False):
        webbrowser.open(html_path.resolve().as_uri())


# ---------------------------------------------------------------------------
# Subcommand: run
# ---------------------------------------------------------------------------


async def cmd_run(args: argparse.Namespace) -> None:
    """Low-level direct access — same internals as audit."""
    await cmd_audit(args)


# ---------------------------------------------------------------------------
# Subcommand: pipeline
# ---------------------------------------------------------------------------


async def cmd_pipeline(args: argparse.Namespace) -> None:
    """End-to-end pipeline: resolve URLs, analyze, write data, generate HTML report."""

    # --- Phase 1: Resolve sources ---
    source_args = getattr(args, "source", [])
    explicit_sitemap = getattr(args, "sitemap", None)
    sitemap_target = explicit_sitemap
    plain_urls: list[str] = []

    if not explicit_sitemap and len(source_args) == 1 and _looks_like_sitemap(source_args[0]):
        # Single positional arg that looks like a sitemap
        sitemap_target = source_args[0]
    else:
        plain_urls = source_args

    # --- Phase 2: Load URLs ---
    urls = await load_urls(
        plain_urls,
        getattr(args, "file", None),
        sitemap=sitemap_target,
        sitemap_limit=getattr(args, "sitemap_limit", None),
        sitemap_filter=getattr(args, "sitemap_filter", None),
        verbose=getattr(args, "verbose", False),
    )

    strategies = [args.strategy] if args.strategy != "both" else ["mobile", "desktop"]
    categories = getattr(args, "categories", DEFAULT_CATEGORIES)

    # --- Phase 3: Analyze ---
    err_console.print(
        f"Pipeline: analyzing [bold]{len(urls)}[/bold] URL(s) · strategy: [cyan]{args.strategy}[/cyan]"
    )
    dataframe = await process_urls(
        urls=urls,
        api_key=args.api_key,
        strategies=strategies,
        categories=categories,
        delay=args.delay,
        workers=args.workers,
        verbose=args.verbose,
    )

    # --- Phase 4: Write data files + print summary ---
    strategy_label = args.strategy if args.strategy != "both" else "both"
    output_format = getattr(args, "output_format", DEFAULT_OUTPUT_FORMAT)
    output_dir = getattr(args, "output_dir", DEFAULT_OUTPUT_DIR)
    explicit_output = getattr(args, "output", None)

    _write_data_files(dataframe, output_format, output_dir, explicit_output, strategy_label)
    _print_audit_summary(dataframe)

    # --- Phase 5: Generate HTML report ---
    if not getattr(args, "no_report", False):
        html_content = generate_html_report(dataframe)
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        html_dir = Path(output_dir)
        html_dir.mkdir(parents=True, exist_ok=True)
        html_path = html_dir / f"{timestamp}-report.html"
        html_path.write_text(html_content)
        err_console.print(f"  [green]✓[/green] HTML report: [cyan]{html_path}[/cyan]")

        if getattr(args, "open_browser", False):
            webbrowser.open(html_path.resolve().as_uri())

    # --- Phase 6: Budget evaluation ---
    if getattr(args, "budget", None):
        exit_code = await _apply_budget(dataframe, args)
        sys.exit(exit_code)


# ---------------------------------------------------------------------------
# Subcommand: budget
# ---------------------------------------------------------------------------


async def cmd_budget(args: argparse.Namespace) -> None:
    """Evaluate existing results against a performance budget (no API calls)."""
    dataframe = load_report(args.input_file)
    exit_code = await _apply_budget(dataframe, args)
    sys.exit(exit_code)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> None:
    parser = build_argument_parser()
    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(0)

    # Load config
    config_path = Path(args.config) if args.config else discover_config_path()
    config = load_config(config_path)

    # Apply profile and config defaults
    profile_name = getattr(args, "profile", None)
    args = apply_profile(args, config, profile_name)

    # Dispatch to subcommand
    commands = {
        "quick-check": cmd_quick_check,
        "audit": cmd_audit,
        "compare": cmd_compare,
        "report": cmd_report,
        "run": cmd_run,
        "pipeline": cmd_pipeline,
        "budget": cmd_budget,
    }

    handler = commands.get(args.command)
    if handler:
        if asyncio.iscoroutinefunction(handler):
            asyncio.run(handler(args))
        else:
            handler(args)
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
