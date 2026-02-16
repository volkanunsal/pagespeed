# /// script
# requires-python = ">=3.13"
# dependencies = [
#   "requests",
#   "pandas",
# ]
# ///
"""Unit tests for pagespeed_insights_tool.py.

Run all tests:
    uv run test_pagespeed_insights_tool.py -v

Run a single class:
    uv run test_pagespeed_insights_tool.py -v TestValidateUrl

All external I/O (API calls, sitemap fetches, file reads) is mocked —
tests run fast and fully offline.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
import textwrap
import unittest
from io import StringIO
from pathlib import Path
from unittest.mock import MagicMock, patch

import pandas as pd

import pagespeed_insights_tool as pst

# ---------------------------------------------------------------------------
# Shared Fixtures
# ---------------------------------------------------------------------------

FULL_API_RESPONSE = {
    "lighthouseResult": {
        "fetchTime": "2026-02-16T12:00:00.000Z",
        "categories": {
            "performance": {"score": 0.92},
            "accessibility": {"score": 0.85},
            "best-practices": {"score": 0.78},
            "seo": {"score": 0.95},
        },
        "audits": {
            "first-contentful-paint": {"numericValue": 1234.5},
            "largest-contentful-paint": {"numericValue": 2345.6},
            "cumulative-layout-shift": {"numericValue": 0.05123},
            "speed-index": {"numericValue": 3456.7},
            "total-blocking-time": {"numericValue": 123.4},
            "interactive": {"numericValue": 4567.8},
        },
    },
    "loadingExperience": {
        "metrics": {
            "FIRST_CONTENTFUL_PAINT_MS": {"percentile": 1800, "category": "FAST"},
            "LARGEST_CONTENTFUL_PAINT_MS": {"percentile": 2500, "category": "AVERAGE"},
            "CUMULATIVE_LAYOUT_SHIFT_SCORE": {"percentile": 10, "category": "FAST"},
            "INTERACTION_TO_NEXT_PAINT": {"percentile": 200, "category": "FAST"},
            "FIRST_INPUT_DELAY_MS": {"percentile": 50, "category": "FAST"},
            "EXPERIMENTAL_TIME_TO_FIRST_BYTE": {"percentile": 800, "category": "AVERAGE"},
        },
    },
}

MINIMAL_API_RESPONSE = {
    "lighthouseResult": {
        "categories": {
            "performance": {"score": 0.55},
        },
        "audits": {},
    },
}

SAMPLE_SITEMAP_URLSET = textwrap.dedent("""\
    <?xml version="1.0" encoding="UTF-8"?>
    <urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
      <url><loc>https://example.com/page1</loc></url>
      <url><loc>https://example.com/page2</loc></url>
      <url><loc>https://example.com/page3</loc></url>
    </urlset>
""")

SAMPLE_SITEMAP_URLSET_NO_NS = textwrap.dedent("""\
    <?xml version="1.0" encoding="UTF-8"?>
    <urlset>
      <url><loc>https://example.com/a</loc></url>
      <url><loc>https://example.com/b</loc></url>
      <url><loc>https://example.com/c</loc></url>
    </urlset>
""")

SAMPLE_SITEMAP_INDEX = textwrap.dedent("""\
    <?xml version="1.0" encoding="UTF-8"?>
    <sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
      <sitemap><loc>https://example.com/sitemap-pages.xml</loc></sitemap>
      <sitemap><loc>https://example.com/sitemap-posts.xml</loc></sitemap>
    </sitemapindex>
""")


def _sample_dataframe() -> pd.DataFrame:
    """Build a 2-row DataFrame with realistic metrics."""
    rows = [
        {
            "url": "https://example.com",
            "strategy": "mobile",
            "error": None,
            "performance_score": 92,
            "accessibility_score": 85,
            "best_practices_score": 78,
            "seo_score": 95,
            "lab_fcp_ms": 1235,
            "lab_lcp_ms": 2346,
            "lab_cls": 0.0512,
            "lab_speed_index_ms": 3457,
            "lab_tbt_ms": 123,
            "lab_tti_ms": 4568,
            "field_fcp_ms": 1800,
            "field_fcp_category": "FAST",
            "field_lcp_ms": 2500,
            "field_lcp_category": "AVERAGE",
            "field_cls": 0.1,
            "field_cls_category": "FAST",
            "field_inp_ms": 200,
            "field_inp_category": "FAST",
            "field_fid_ms": 50,
            "field_fid_category": "FAST",
            "field_ttfb_ms": 800,
            "field_ttfb_category": "AVERAGE",
            "fetch_time": "2026-02-16T12:00:00.000Z",
        },
        {
            "url": "https://example.com",
            "strategy": "desktop",
            "error": None,
            "performance_score": 98,
            "accessibility_score": 85,
            "best_practices_score": 78,
            "seo_score": 95,
            "lab_fcp_ms": 800,
            "lab_lcp_ms": 1200,
            "lab_cls": 0.0010,
            "lab_speed_index_ms": 1000,
            "lab_tbt_ms": 50,
            "lab_tti_ms": 1500,
            "field_fcp_ms": 1200,
            "field_fcp_category": "FAST",
            "field_lcp_ms": 1800,
            "field_lcp_category": "FAST",
            "field_cls": 0.05,
            "field_cls_category": "FAST",
            "field_inp_ms": 100,
            "field_inp_category": "FAST",
            "field_fid_ms": 30,
            "field_fid_category": "FAST",
            "field_ttfb_ms": 500,
            "field_ttfb_category": "FAST",
            "fetch_time": "2026-02-16T12:00:01.000Z",
        },
    ]
    return pd.DataFrame(rows)


def _make_mock_response(status_code, json_data=None, headers=None, text=""):
    """Factory for mock requests.Response objects."""
    response = MagicMock()
    response.status_code = status_code
    response.headers = headers or {}
    response.text = text
    if json_data is not None:
        response.json.return_value = json_data
    else:
        response.json.side_effect = ValueError("No JSON")
    return response


# ===================================================================
# 1. TestValidateUrl
# ===================================================================


class TestValidateUrl(unittest.TestCase):
    """Tests for validate_url() — pure function, no mocking needed."""

    def test_valid_https_url(self):
        self.assertEqual(pst.validate_url("https://example.com"), "https://example.com")

    def test_valid_http_url(self):
        self.assertEqual(pst.validate_url("http://example.com"), "http://example.com")

    def test_schemeless_gets_https(self):
        self.assertEqual(pst.validate_url("example.com"), "https://example.com")

    def test_empty_string_returns_none(self):
        self.assertIsNone(pst.validate_url(""))

    def test_comment_returns_none(self):
        self.assertIsNone(pst.validate_url("# this is a comment"))

    def test_no_tld_returns_none(self):
        self.assertIsNone(pst.validate_url("localhost"))

    def test_whitespace_stripped(self):
        self.assertEqual(pst.validate_url("  https://example.com  "), "https://example.com")

    def test_complex_url_preserved(self):
        complex_url = "https://example.com/path?query=1&foo=bar#fragment"
        self.assertEqual(pst.validate_url(complex_url), complex_url)


# ===================================================================
# 2. TestExtractMetrics
# ===================================================================


class TestExtractMetrics(unittest.TestCase):
    """Tests for extract_metrics() — pure function operating on API JSON."""

    def test_full_extraction(self):
        result = pst.extract_metrics(FULL_API_RESPONSE, "https://example.com", "mobile")
        self.assertEqual(result["url"], "https://example.com")
        self.assertEqual(result["strategy"], "mobile")
        self.assertIsNone(result["error"])
        self.assertEqual(result["fetch_time"], "2026-02-16T12:00:00.000Z")

    def test_performance_score_multiplied_by_100(self):
        result = pst.extract_metrics(FULL_API_RESPONSE, "https://example.com", "mobile")
        self.assertEqual(result["performance_score"], 92)

    def test_category_scores(self):
        result = pst.extract_metrics(FULL_API_RESPONSE, "https://example.com", "mobile")
        self.assertEqual(result["accessibility_score"], 85)
        self.assertEqual(result["best_practices_score"], 78)
        self.assertEqual(result["seo_score"], 95)

    def test_lab_metrics_rounded(self):
        result = pst.extract_metrics(FULL_API_RESPONSE, "https://example.com", "mobile")
        self.assertEqual(result["lab_fcp_ms"], 1234)  # round(1234.5) — banker's rounding
        self.assertEqual(result["lab_lcp_ms"], 2346)  # round(2345.6)
        self.assertEqual(result["lab_tbt_ms"], 123)   # round(123.4)

    def test_lab_cls_rounded_to_4_decimals(self):
        result = pst.extract_metrics(FULL_API_RESPONSE, "https://example.com", "mobile")
        self.assertEqual(result["lab_cls"], 0.0512)  # round(0.05123, 4)

    def test_field_cls_stored_as_percentile(self):
        # "CLS" is not a substring of "CUMULATIVE_LAYOUT_SHIFT_SCORE",
        # so the /100 division does not trigger — percentile stored as-is.
        result = pst.extract_metrics(FULL_API_RESPONSE, "https://example.com", "mobile")
        self.assertEqual(result["field_cls"], 10)

    def test_minimal_response_missing_data_graceful(self):
        result = pst.extract_metrics(MINIMAL_API_RESPONSE, "https://test.com", "desktop")
        self.assertEqual(result["performance_score"], 55)
        self.assertIsNone(result["lab_fcp_ms"])
        self.assertIsNone(result["lab_lcp_ms"])
        self.assertIsNone(result["field_fcp_ms"])
        self.assertIsNone(result["field_cls"])

    def test_none_score_preserved(self):
        no_score_response = {"lighthouseResult": {"categories": {"performance": {}}}}
        result = pst.extract_metrics(no_score_response, "https://x.com", "mobile")
        self.assertIsNone(result["performance_score"])


# ===================================================================
# 3. TestFormatTerminalTable
# ===================================================================


class TestFormatTerminalTable(unittest.TestCase):
    """Tests for format_terminal_table() — pure string formatting."""

    def test_single_dict_input(self):
        metrics = {
            "url": "https://example.com",
            "strategy": "mobile",
            "error": None,
            "performance_score": 92,
            "lab_fcp_ms": 1200,
        }
        output = pst.format_terminal_table(metrics)
        self.assertIn("https://example.com", output)
        self.assertIn("mobile", output)
        self.assertIn("92/100", output)

    def test_list_input(self):
        metrics_list = [
            {"url": "https://a.com", "strategy": "mobile", "error": None, "performance_score": 90},
            {"url": "https://b.com", "strategy": "desktop", "error": None, "performance_score": 50},
        ]
        output = pst.format_terminal_table(metrics_list)
        self.assertIn("https://a.com", output)
        self.assertIn("https://b.com", output)

    def test_error_row(self):
        metrics = {"url": "https://fail.com", "strategy": "mobile", "error": "HTTP 500"}
        output = pst.format_terminal_table(metrics)
        self.assertIn("HTTP 500", output)
        # Error rows don't show lab data
        self.assertNotIn("Lab Data", output)

    def test_score_indicator_good(self):
        metrics = {"url": "https://x.com", "strategy": "mobile", "error": None, "performance_score": 95}
        output = pst.format_terminal_table(metrics)
        self.assertIn("GOOD", output)

    def test_score_indicator_needs_work(self):
        metrics = {"url": "https://x.com", "strategy": "mobile", "error": None, "performance_score": 60}
        output = pst.format_terminal_table(metrics)
        self.assertIn("NEEDS WORK", output)

    def test_score_indicator_poor(self):
        metrics = {"url": "https://x.com", "strategy": "mobile", "error": None, "performance_score": 30}
        output = pst.format_terminal_table(metrics)
        self.assertIn("POOR", output)


# ===================================================================
# 4. TestGenerateHtmlReport
# ===================================================================


class TestGenerateHtmlReport(unittest.TestCase):
    """Tests for generate_html_report() — pure string generation."""

    def setUp(self):
        self.dataframe = _sample_dataframe()

    def test_doctype_and_html_tags(self):
        html = pst.generate_html_report(self.dataframe)
        self.assertTrue(html.startswith("<!DOCTYPE html>"))
        self.assertIn("</html>", html)

    def test_urls_present(self):
        html = pst.generate_html_report(self.dataframe)
        self.assertIn("https://example.com", html)

    def test_score_color_classes(self):
        html = pst.generate_html_report(self.dataframe)
        # 92 and 98 are both "good"
        self.assertIn('class="score-cell good"', html)

    def test_cwv_pass_indicators(self):
        html = pst.generate_html_report(self.dataframe)
        self.assertIn("cwv-good", html)

    def test_field_section_present(self):
        html = pst.generate_html_report(self.dataframe)
        self.assertIn("Field Data (CrUX)", html)

    def test_field_section_absent_when_no_field_data(self):
        # Build a DataFrame with no field data
        rows = [{
            "url": "https://example.com",
            "strategy": "mobile",
            "error": None,
            "performance_score": 80,
            "lab_fcp_ms": 1200,
            "lab_lcp_ms": 2000,
            "lab_cls": 0.05,
            "lab_speed_index_ms": 3000,
            "lab_tbt_ms": 100,
            "lab_tti_ms": 3500,
        }]
        no_field_df = pd.DataFrame(rows)
        html = pst.generate_html_report(no_field_df)
        self.assertNotIn("Field Data (CrUX)", html)

    def test_error_row_in_table(self):
        rows = [{
            "url": "https://fail.com",
            "strategy": "mobile",
            "error": "HTTP 500 for https://fail.com",
            "performance_score": None,
        }]
        error_df = pd.DataFrame(rows)
        html = pst.generate_html_report(error_df)
        self.assertIn("error-cell", html)
        self.assertIn("HTTP 500", html)


# ===================================================================
# 5. TestLoadConfig
# ===================================================================


class TestLoadConfig(unittest.TestCase):
    """Tests for load_config()."""

    def test_none_path_returns_empty(self):
        self.assertEqual(pst.load_config(None), {})

    def test_valid_toml_parsed(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".toml", delete=False) as fh:
            fh.write('[settings]\napi_key = "test123"\ndelay = 2.0\n')
            fh.flush()
            config = pst.load_config(Path(fh.name))
        os.unlink(fh.name)
        self.assertEqual(config["settings"]["api_key"], "test123")
        self.assertEqual(config["settings"]["delay"], 2.0)

    def test_malformed_toml_exits(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".toml", delete=False) as fh:
            fh.write("this is not valid TOML {{{\n")
            fh.flush()
            with self.assertRaises(SystemExit):
                pst.load_config(Path(fh.name))
        os.unlink(fh.name)

    def test_unreadable_file_exits(self):
        fake_path = Path("/tmp/nonexistent_config_xyzzy_42.toml")
        with self.assertRaises(SystemExit):
            pst.load_config(fake_path)


# ===================================================================
# 6. TestApplyProfile
# ===================================================================


class TestApplyProfile(unittest.TestCase):
    """Tests for apply_profile()."""

    def _make_args(self, explicit=None, **kwargs):
        """Build a Namespace simulating parsed CLI args."""
        defaults = {
            "api_key": None,
            "file": None,
            "delay": 1.5,
            "strategy": "mobile",
            "output_format": "csv",
            "output_dir": "./reports",
            "workers": 4,
            "categories": ["performance"],
            "verbose": False,
            "sitemap": None,
            "sitemap_limit": None,
            "sitemap_filter": None,
        }
        defaults.update(kwargs)
        args = argparse.Namespace(**defaults)
        args._explicit_args = list(explicit) if explicit else []
        return args

    def test_empty_config_preserves_defaults(self):
        args = self._make_args()
        result = pst.apply_profile(args, {}, None)
        self.assertEqual(result.delay, 1.5)
        self.assertEqual(result.strategy, "mobile")

    def test_settings_fill_unset(self):
        args = self._make_args()
        config = {"settings": {"delay": 3.0, "strategy": "desktop"}}
        result = pst.apply_profile(args, config, None)
        self.assertEqual(result.delay, 3.0)
        self.assertEqual(result.strategy, "desktop")

    def test_profile_overrides_settings(self):
        args = self._make_args()
        config = {
            "settings": {"delay": 3.0},
            "profiles": {"fast": {"delay": 0.5}},
        }
        result = pst.apply_profile(args, config, "fast")
        self.assertEqual(result.delay, 0.5)

    def test_cli_explicit_overrides_all(self):
        args = self._make_args(explicit=["delay"], delay=5.0)
        config = {
            "settings": {"delay": 3.0},
            "profiles": {"fast": {"delay": 0.5}},
        }
        result = pst.apply_profile(args, config, "fast")
        self.assertEqual(result.delay, 5.0)

    def test_missing_profile_exits(self):
        args = self._make_args()
        config = {"profiles": {"existing": {"delay": 1.0}}}
        with self.assertRaises(SystemExit):
            pst.apply_profile(args, config, "nonexistent")

    @patch.dict(os.environ, {"PAGESPEED_API_KEY": "env_key_123"})
    def test_env_var_fallback(self):
        args = self._make_args()
        result = pst.apply_profile(args, {}, None)
        self.assertEqual(result.api_key, "env_key_123")

    @patch.dict(os.environ, {"PAGESPEED_API_KEY": "env_key_123"})
    def test_config_api_key_not_overridden_by_env(self):
        args = self._make_args()
        config = {"settings": {"api_key": "config_key_456"}}
        result = pst.apply_profile(args, config, None)
        # Config key takes priority — env is only used when api_key is not set
        self.assertEqual(result.api_key, "config_key_456")


# ===================================================================
# 7. TestDiscoverConfigPath
# ===================================================================


class TestDiscoverConfigPath(unittest.TestCase):
    """Tests for discover_config_path()."""

    def test_no_config_returns_none(self):
        # Patch CONFIG_SEARCH_PATHS to empty dirs
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch.object(pst, "CONFIG_SEARCH_PATHS", [Path(tmpdir)]):
                result = pst.discover_config_path()
        self.assertIsNone(result)

    def test_cwd_config_found(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            config_file = Path(tmpdir) / "pagespeed.toml"
            config_file.write_text('[settings]\ndelay = 1.0\n')
            with patch.object(pst, "CONFIG_SEARCH_PATHS", [Path(tmpdir)]):
                result = pst.discover_config_path()
            self.assertEqual(result, config_file)

    def test_second_search_path_found(self):
        with tempfile.TemporaryDirectory() as empty_dir, tempfile.TemporaryDirectory() as config_dir:
            config_file = Path(config_dir) / "pagespeed.toml"
            config_file.write_text('[settings]\ndelay = 2.0\n')
            with patch.object(pst, "CONFIG_SEARCH_PATHS", [Path(empty_dir), Path(config_dir)]):
                result = pst.discover_config_path()
            self.assertEqual(result, config_file)


# ===================================================================
# 8. TestTrackingAction
# ===================================================================


class TestTrackingAction(unittest.TestCase):
    """Tests for TrackingAction and TrackingStoreTrueAction."""

    def test_tracking_action_records_dest(self):
        parser = argparse.ArgumentParser()
        parser.add_argument("--delay", dest="delay", action=pst.TrackingAction, type=float, default=1.5)
        args = parser.parse_args(["--delay", "3.0"])
        self.assertEqual(args.delay, 3.0)
        self.assertIn("delay", args._explicit_args)

    def test_tracking_store_true_records_dest(self):
        parser = argparse.ArgumentParser()
        parser.add_argument("--verbose", dest="verbose", action=pst.TrackingStoreTrueAction, default=False)
        args = parser.parse_args(["--verbose"])
        self.assertTrue(args.verbose)
        self.assertIn("verbose", args._explicit_args)

    def test_unset_flags_not_tracked(self):
        parser = argparse.ArgumentParser()
        parser.add_argument("--delay", dest="delay", action=pst.TrackingAction, type=float, default=1.5)
        parser.add_argument("--verbose", dest="verbose", action=pst.TrackingStoreTrueAction, default=False)
        args = parser.parse_args([])
        explicit = getattr(args, "_explicit_args", [])
        self.assertNotIn("delay", explicit)
        self.assertNotIn("verbose", explicit)


# ===================================================================
# 9. TestBuildArgumentParser
# ===================================================================


class TestBuildArgumentParser(unittest.TestCase):
    """Tests for build_argument_parser()."""

    def setUp(self):
        self.parser = pst.build_argument_parser()

    def test_quick_check_parses(self):
        args = self.parser.parse_args(["quick-check", "https://example.com"])
        self.assertEqual(args.command, "quick-check")
        self.assertEqual(args.url, "https://example.com")

    def test_audit_parses(self):
        args = self.parser.parse_args(["audit", "https://a.com", "https://b.com", "-s", "both"])
        self.assertEqual(args.command, "audit")
        self.assertEqual(args.urls, ["https://a.com", "https://b.com"])
        self.assertEqual(args.strategy, "both")

    def test_compare_parses(self):
        args = self.parser.parse_args(["compare", "before.csv", "after.csv", "--threshold", "10"])
        self.assertEqual(args.command, "compare")
        self.assertEqual(args.before, "before.csv")
        self.assertEqual(args.after, "after.csv")
        self.assertEqual(args.threshold, 10.0)

    def test_report_parses(self):
        args = self.parser.parse_args(["report", "results.csv", "--open"])
        self.assertEqual(args.command, "report")
        self.assertEqual(args.input_file, "results.csv")
        self.assertTrue(args.open_browser)

    def test_default_values(self):
        args = self.parser.parse_args(["audit"])
        self.assertEqual(args.strategy, "mobile")
        self.assertEqual(args.delay, 1.5)
        self.assertEqual(args.workers, 4)
        self.assertEqual(args.output_format, "csv")
        self.assertEqual(args.output_dir, "./reports")


# ===================================================================
# 10. TestParseSitemapXml
# ===================================================================


class TestParseSitemapXml(unittest.TestCase):
    """Tests for parse_sitemap_xml()."""

    def test_namespaced_urlset(self):
        urls = pst.parse_sitemap_xml(SAMPLE_SITEMAP_URLSET)
        self.assertEqual(len(urls), 3)
        self.assertEqual(urls[0], "https://example.com/page1")

    def test_no_namespace_urlset(self):
        urls = pst.parse_sitemap_xml(SAMPLE_SITEMAP_URLSET_NO_NS)
        self.assertEqual(len(urls), 3)
        self.assertEqual(urls[0], "https://example.com/a")

    @patch("pagespeed_insights_tool._fetch_sitemap_content")
    def test_sitemapindex_recursive_fetch(self, mock_fetch):
        child_sitemap = textwrap.dedent("""\
            <?xml version="1.0" encoding="UTF-8"?>
            <urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
              <url><loc>https://example.com/from-child</loc></url>
            </urlset>
        """)
        mock_fetch.return_value = child_sitemap
        urls = pst.parse_sitemap_xml(SAMPLE_SITEMAP_INDEX)
        self.assertEqual(mock_fetch.call_count, 2)
        self.assertEqual(len(urls), 2)  # 1 URL from each child
        self.assertEqual(urls[0], "https://example.com/from-child")

    @patch("pagespeed_insights_tool._fetch_sitemap_content")
    def test_child_fetch_failure(self, mock_fetch):
        import requests as real_requests
        mock_fetch.side_effect = real_requests.RequestException("Connection error")
        urls = pst.parse_sitemap_xml(SAMPLE_SITEMAP_INDEX)
        self.assertEqual(urls, [])

    def test_max_depth_reached(self):
        urls = pst.parse_sitemap_xml(SAMPLE_SITEMAP_INDEX, _depth=pst.MAX_SITEMAP_DEPTH)
        self.assertEqual(urls, [])

    def test_malformed_xml(self):
        urls = pst.parse_sitemap_xml("<not valid xml<<<")
        self.assertEqual(urls, [])

    def test_empty_locs_skipped(self):
        xml = textwrap.dedent("""\
            <?xml version="1.0" encoding="UTF-8"?>
            <urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
              <url><loc>https://example.com/valid</loc></url>
              <url><loc>  </loc></url>
              <url><loc></loc></url>
            </urlset>
        """)
        urls = pst.parse_sitemap_xml(xml)
        self.assertEqual(urls, ["https://example.com/valid"])

    @patch("pagespeed_insights_tool._fetch_sitemap_content")
    def test_no_namespace_sitemapindex(self, mock_fetch):
        index_xml = textwrap.dedent("""\
            <?xml version="1.0" encoding="UTF-8"?>
            <sitemapindex>
              <sitemap><loc>https://example.com/child.xml</loc></sitemap>
            </sitemapindex>
        """)
        child_xml = textwrap.dedent("""\
            <?xml version="1.0" encoding="UTF-8"?>
            <urlset>
              <url><loc>https://example.com/page</loc></url>
            </urlset>
        """)
        mock_fetch.return_value = child_xml
        urls = pst.parse_sitemap_xml(index_xml)
        self.assertEqual(urls, ["https://example.com/page"])


# ===================================================================
# 11. TestFetchSitemapUrls
# ===================================================================


class TestFetchSitemapUrls(unittest.TestCase):
    """Tests for fetch_sitemap_urls()."""

    @patch("pagespeed_insights_tool._fetch_sitemap_content")
    def test_basic_fetch_and_parse(self, mock_fetch):
        mock_fetch.return_value = SAMPLE_SITEMAP_URLSET
        urls = pst.fetch_sitemap_urls("https://example.com/sitemap.xml")
        self.assertEqual(len(urls), 3)

    @patch("pagespeed_insights_tool._fetch_sitemap_content")
    def test_regex_filter(self, mock_fetch):
        mock_fetch.return_value = SAMPLE_SITEMAP_URLSET
        urls = pst.fetch_sitemap_urls("https://example.com/sitemap.xml", filter_pattern=r"page[12]$")
        self.assertEqual(len(urls), 2)

    @patch("pagespeed_insights_tool._fetch_sitemap_content")
    def test_limit(self, mock_fetch):
        mock_fetch.return_value = SAMPLE_SITEMAP_URLSET
        urls = pst.fetch_sitemap_urls("https://example.com/sitemap.xml", limit=2)
        self.assertEqual(len(urls), 2)

    @patch("pagespeed_insights_tool._fetch_sitemap_content")
    def test_invalid_regex_returns_empty(self, mock_fetch):
        mock_fetch.return_value = SAMPLE_SITEMAP_URLSET
        urls = pst.fetch_sitemap_urls("https://example.com/sitemap.xml", filter_pattern="[invalid")
        self.assertEqual(urls, [])

    @patch("pagespeed_insights_tool._fetch_sitemap_content")
    def test_fetch_failure_returns_empty(self, mock_fetch):
        import requests as real_requests
        mock_fetch.side_effect = real_requests.RequestException("timeout")
        urls = pst.fetch_sitemap_urls("https://example.com/sitemap.xml")
        self.assertEqual(urls, [])


# ===================================================================
# 12. TestLoadUrls
# ===================================================================


class TestLoadUrls(unittest.TestCase):
    """Tests for load_urls()."""

    def test_from_args(self):
        urls = pst.load_urls(["https://example.com"], None)
        self.assertEqual(urls, ["https://example.com"])

    def test_from_file(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as fh:
            fh.write("https://a.com\nhttps://b.com\n")
            fh.flush()
            urls = pst.load_urls([], fh.name)
        os.unlink(fh.name)
        self.assertEqual(urls, ["https://a.com", "https://b.com"])

    def test_from_stdin(self):
        mock_stdin = StringIO("https://stdin.com\n")
        mock_stdin.isatty = lambda: False
        with patch("sys.stdin", mock_stdin):
            urls = pst.load_urls([], None, allow_stdin=True)
        self.assertEqual(urls, ["https://stdin.com"])

    @patch("pagespeed_insights_tool.fetch_sitemap_urls")
    def test_from_sitemap(self, mock_sitemap):
        mock_sitemap.return_value = ["https://example.com/from-sitemap"]
        urls = pst.load_urls(["https://example.com"], None, sitemap="https://example.com/sitemap.xml")
        self.assertIn("https://example.com", urls)
        self.assertIn("https://example.com/from-sitemap", urls)

    def test_deduplication(self):
        urls = pst.load_urls(["https://example.com", "https://example.com"], None)
        self.assertEqual(len(urls), 1)

    def test_invalid_urls_skipped(self):
        urls = pst.load_urls(["https://example.com", "not-a-url", "https://valid.org"], None)
        self.assertEqual(urls, ["https://example.com", "https://valid.org"])

    def test_no_valid_urls_exits(self):
        with self.assertRaises(SystemExit):
            pst.load_urls(["not-a-url"], None)

    def test_file_not_found_exits(self):
        with self.assertRaises(SystemExit):
            pst.load_urls([], "/tmp/nonexistent_urls_xyzzy_42.txt")

    def test_args_takes_priority_over_file(self):
        """When url_args are provided, file_path is not read."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as fh:
            fh.write("https://from-file.com\n")
            fh.flush()
            urls = pst.load_urls(["https://from-args.com"], fh.name)
        os.unlink(fh.name)
        self.assertEqual(urls, ["https://from-args.com"])
        self.assertNotIn("https://from-file.com", urls)


# ===================================================================
# 13. TestFetchPagespeedResult
# ===================================================================


class TestFetchPagespeedResult(unittest.TestCase):
    """Tests for fetch_pagespeed_result() — mocks requests.get and time.sleep."""

    @patch("pagespeed_insights_tool.time.sleep")
    @patch("pagespeed_insights_tool.requests.get")
    def test_success_first_attempt(self, mock_get, mock_sleep):
        mock_get.return_value = _make_mock_response(200, json_data=FULL_API_RESPONSE)
        result = pst.fetch_pagespeed_result("https://example.com", "mobile")
        self.assertEqual(result, FULL_API_RESPONSE)
        mock_sleep.assert_not_called()

    @patch("pagespeed_insights_tool.time.sleep")
    @patch("pagespeed_insights_tool.requests.get")
    def test_429_with_retry_after(self, mock_get, mock_sleep):
        rate_limited = _make_mock_response(429, headers={"Retry-After": "5"})
        success = _make_mock_response(200, json_data=FULL_API_RESPONSE)
        mock_get.side_effect = [rate_limited, success]
        result = pst.fetch_pagespeed_result("https://example.com", "mobile")
        self.assertEqual(result, FULL_API_RESPONSE)
        mock_sleep.assert_called_once_with(5.0)

    @patch("pagespeed_insights_tool.time.sleep")
    @patch("pagespeed_insights_tool.requests.get")
    def test_500_exponential_backoff(self, mock_get, mock_sleep):
        error_500 = _make_mock_response(500)
        success = _make_mock_response(200, json_data=FULL_API_RESPONSE)
        mock_get.side_effect = [error_500, success]
        result = pst.fetch_pagespeed_result("https://example.com", "mobile")
        self.assertEqual(result, FULL_API_RESPONSE)
        # First attempt: wait_time = 2.0 * (2**0) = 2.0
        mock_sleep.assert_called_once_with(2.0)

    @patch("pagespeed_insights_tool.time.sleep")
    @patch("pagespeed_insights_tool.requests.get")
    def test_503_retry(self, mock_get, mock_sleep):
        error_503 = _make_mock_response(503)
        success = _make_mock_response(200, json_data=FULL_API_RESPONSE)
        mock_get.side_effect = [error_503, success]
        result = pst.fetch_pagespeed_result("https://example.com", "mobile")
        self.assertEqual(result, FULL_API_RESPONSE)

    @patch("pagespeed_insights_tool.time.sleep")
    @patch("pagespeed_insights_tool.requests.get")
    def test_non_retryable_error_403(self, mock_get, mock_sleep):
        forbidden = _make_mock_response(403, json_data={"error": {"message": "Forbidden"}})
        mock_get.return_value = forbidden
        with self.assertRaises(pst.PageSpeedError) as ctx:
            pst.fetch_pagespeed_result("https://example.com", "mobile")
        self.assertIn("403", str(ctx.exception))

    @patch("pagespeed_insights_tool.time.sleep")
    @patch("pagespeed_insights_tool.requests.get")
    def test_max_retries_exhausted(self, mock_get, mock_sleep):
        error_500 = _make_mock_response(500)
        mock_get.return_value = error_500
        with self.assertRaises(pst.PageSpeedError) as ctx:
            pst.fetch_pagespeed_result("https://example.com", "mobile")
        # On the last attempt, falls through to non-retryable raise
        self.assertIn("HTTP 500", str(ctx.exception))
        # Sleep is called between retries (MAX_RETRIES times)
        self.assertEqual(mock_sleep.call_count, pst.MAX_RETRIES)

    @patch("pagespeed_insights_tool.time.sleep")
    @patch("pagespeed_insights_tool.requests.get")
    def test_request_exception_retried(self, mock_get, mock_sleep):
        import requests as real_requests
        mock_get.side_effect = [
            real_requests.ConnectionError("DNS failure"),
            _make_mock_response(200, json_data=FULL_API_RESPONSE),
        ]
        result = pst.fetch_pagespeed_result("https://example.com", "mobile")
        self.assertEqual(result, FULL_API_RESPONSE)
        mock_sleep.assert_called_once()


# ===================================================================
# 14. TestProcessUrls
# ===================================================================


class TestProcessUrls(unittest.TestCase):
    """Tests for process_urls() — mocks fetch_pagespeed_result."""

    @patch("pagespeed_insights_tool.time.sleep")
    @patch("pagespeed_insights_tool.time.monotonic")
    @patch("pagespeed_insights_tool.fetch_pagespeed_result")
    def test_single_url(self, mock_fetch, mock_monotonic, mock_sleep):
        mock_monotonic.side_effect = [0.0, 0.0, 2.0, 2.0]
        mock_fetch.return_value = FULL_API_RESPONSE
        df = pst.process_urls(
            urls=["https://example.com"],
            api_key=None,
            strategies=["mobile"],
            categories=["performance"],
            delay=1.5,
            workers=1,
        )
        self.assertEqual(len(df), 1)
        self.assertEqual(df.iloc[0]["url"], "https://example.com")

    @patch("pagespeed_insights_tool.time.sleep")
    @patch("pagespeed_insights_tool.time.monotonic")
    @patch("pagespeed_insights_tool.fetch_pagespeed_result")
    def test_multiple_urls_and_strategies(self, mock_fetch, mock_monotonic, mock_sleep):
        mock_monotonic.return_value = 0.0
        mock_fetch.return_value = FULL_API_RESPONSE
        df = pst.process_urls(
            urls=["https://a.com", "https://b.com"],
            api_key=None,
            strategies=["mobile", "desktop"],
            categories=["performance"],
            delay=0.0,
            workers=1,
        )
        self.assertEqual(len(df), 4)  # 2 URLs * 2 strategies

    @patch("pagespeed_insights_tool.time.sleep")
    @patch("pagespeed_insights_tool.time.monotonic")
    @patch("pagespeed_insights_tool.fetch_pagespeed_result")
    def test_error_handling_per_url(self, mock_fetch, mock_monotonic, mock_sleep):
        mock_monotonic.return_value = 0.0
        mock_fetch.side_effect = [
            FULL_API_RESPONSE,
            pst.PageSpeedError("API error"),
        ]
        df = pst.process_urls(
            urls=["https://good.com", "https://bad.com"],
            api_key=None,
            strategies=["mobile"],
            categories=["performance"],
            delay=0.0,
            workers=1,
        )
        self.assertEqual(len(df), 2)
        self.assertTrue(pd.isna(df.iloc[0].get("error")) or df.iloc[0].get("error") is None)
        self.assertIn("API error", df.iloc[1]["error"])

    @patch("pagespeed_insights_tool.time.sleep")
    @patch("pagespeed_insights_tool.time.monotonic")
    @patch("pagespeed_insights_tool.fetch_pagespeed_result")
    def test_sequential_workers_1(self, mock_fetch, mock_monotonic, mock_sleep):
        mock_monotonic.return_value = 0.0
        mock_fetch.return_value = FULL_API_RESPONSE
        df = pst.process_urls(
            urls=["https://example.com"],
            api_key=None,
            strategies=["mobile"],
            categories=["performance"],
            delay=0.0,
            workers=1,
        )
        self.assertEqual(len(df), 1)

    @patch("pagespeed_insights_tool.time.sleep")
    @patch("pagespeed_insights_tool.time.monotonic")
    @patch("pagespeed_insights_tool.fetch_pagespeed_result")
    def test_concurrent_workers_4(self, mock_fetch, mock_monotonic, mock_sleep):
        mock_monotonic.return_value = 0.0
        mock_fetch.return_value = FULL_API_RESPONSE
        df = pst.process_urls(
            urls=["https://a.com", "https://b.com", "https://c.com", "https://d.com"],
            api_key=None,
            strategies=["mobile"],
            categories=["performance"],
            delay=0.0,
            workers=4,
        )
        self.assertEqual(len(df), 4)


# ===================================================================
# 15. TestGenerateOutputPath
# ===================================================================


class TestGenerateOutputPath(unittest.TestCase):
    """Tests for generate_output_path()."""

    def test_path_format(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch("pagespeed_insights_tool.datetime") as mock_dt:
                mock_now = MagicMock()
                mock_now.strftime.return_value = "20260216T120000Z"
                mock_dt.now.return_value = mock_now
                path = pst.generate_output_path(tmpdir, "mobile", "csv")
            self.assertEqual(path.name, "20260216T120000Z-mobile.csv")

    def test_creates_directory(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            nested_dir = os.path.join(tmpdir, "sub", "dir")
            with patch("pagespeed_insights_tool.datetime") as mock_dt:
                mock_now = MagicMock()
                mock_now.strftime.return_value = "20260216T120000Z"
                mock_dt.now.return_value = mock_now
                path = pst.generate_output_path(nested_dir, "both", "json")
            self.assertTrue(path.parent.exists())

    def test_different_strategy_and_extension(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch("pagespeed_insights_tool.datetime") as mock_dt:
                mock_now = MagicMock()
                mock_now.strftime.return_value = "20260216T120000Z"
                mock_dt.now.return_value = mock_now
                path = pst.generate_output_path(tmpdir, "desktop", "json")
            self.assertEqual(path.name, "20260216T120000Z-desktop.json")


# ===================================================================
# 16. TestOutputCsv
# ===================================================================


class TestOutputCsv(unittest.TestCase):
    """Tests for output_csv()."""

    def test_writes_file(self):
        df = _sample_dataframe()
        with tempfile.TemporaryDirectory() as tmpdir:
            output_path = Path(tmpdir) / "test.csv"
            pst.output_csv(df, output_path)
            self.assertTrue(output_path.exists())
            content = output_path.read_text()
            self.assertIn("https://example.com", content)
            self.assertIn("mobile", content)

    def test_returns_path_string(self):
        df = _sample_dataframe()
        with tempfile.TemporaryDirectory() as tmpdir:
            output_path = Path(tmpdir) / "test.csv"
            result = pst.output_csv(df, output_path)
            self.assertEqual(result, str(output_path))

    def test_creates_parent_directory(self):
        df = _sample_dataframe()
        with tempfile.TemporaryDirectory() as tmpdir:
            output_path = Path(tmpdir) / "nested" / "dir" / "test.csv"
            pst.output_csv(df, output_path)
            self.assertTrue(output_path.exists())


# ===================================================================
# 17. TestOutputJson
# ===================================================================


class TestOutputJson(unittest.TestCase):
    """Tests for output_json()."""

    def test_writes_file(self):
        df = _sample_dataframe()
        with tempfile.TemporaryDirectory() as tmpdir:
            output_path = Path(tmpdir) / "test.json"
            pst.output_json(df, output_path)
            self.assertTrue(output_path.exists())

    def test_metadata_envelope(self):
        df = _sample_dataframe()
        with tempfile.TemporaryDirectory() as tmpdir:
            output_path = Path(tmpdir) / "test.json"
            pst.output_json(df, output_path)
            data = json.loads(output_path.read_text())
            self.assertIn("metadata", data)
            self.assertIn("results", data)
            self.assertEqual(data["metadata"]["total_urls"], 1)
            self.assertIn("mobile", data["metadata"]["strategies"])
            self.assertIn("desktop", data["metadata"]["strategies"])
            self.assertEqual(data["metadata"]["tool_version"], pst.__version__)

    def test_results_structure(self):
        df = _sample_dataframe()
        with tempfile.TemporaryDirectory() as tmpdir:
            output_path = Path(tmpdir) / "test.json"
            pst.output_json(df, output_path)
            data = json.loads(output_path.read_text())
            self.assertEqual(len(data["results"]), 2)
            first = data["results"][0]
            self.assertIn("url", first)
            self.assertIn("strategy", first)
            self.assertIn("performance_score", first)

    def test_nested_lab_metrics(self):
        df = _sample_dataframe()
        with tempfile.TemporaryDirectory() as tmpdir:
            output_path = Path(tmpdir) / "test.json"
            pst.output_json(df, output_path)
            data = json.loads(output_path.read_text())
            first = data["results"][0]
            self.assertIn("lab_metrics", first)
            self.assertIn("lab_fcp_ms", first["lab_metrics"])

    def test_nested_field_metrics(self):
        df = _sample_dataframe()
        with tempfile.TemporaryDirectory() as tmpdir:
            output_path = Path(tmpdir) / "test.json"
            pst.output_json(df, output_path)
            data = json.loads(output_path.read_text())
            first = data["results"][0]
            self.assertIn("field_metrics", first)
            self.assertIn("field_fcp_ms", first["field_metrics"])
            self.assertIn("field_fcp_category", first["field_metrics"])


# ===================================================================
# 18. TestLoadReport
# ===================================================================


class TestLoadReport(unittest.TestCase):
    """Tests for load_report()."""

    def test_load_csv(self):
        df = _sample_dataframe()
        with tempfile.TemporaryDirectory() as tmpdir:
            csv_path = Path(tmpdir) / "data.csv"
            df.to_csv(csv_path, index=False)
            loaded = pst.load_report(str(csv_path))
            self.assertEqual(len(loaded), 2)
            self.assertIn("url", loaded.columns)

    def test_load_structured_json(self):
        df = _sample_dataframe()
        with tempfile.TemporaryDirectory() as tmpdir:
            json_path = Path(tmpdir) / "data.json"
            pst.output_json(df, json_path)
            loaded = pst.load_report(str(json_path))
            self.assertEqual(len(loaded), 2)
            self.assertIn("url", loaded.columns)
            self.assertIn("lab_fcp_ms", loaded.columns)

    def test_load_flat_json(self):
        rows = [
            {"url": "https://example.com", "strategy": "mobile", "performance_score": 90},
        ]
        with tempfile.TemporaryDirectory() as tmpdir:
            json_path = Path(tmpdir) / "flat.json"
            json_path.write_text(json.dumps(rows))
            loaded = pst.load_report(str(json_path))
            self.assertEqual(len(loaded), 1)

    def test_file_not_found_exits(self):
        with self.assertRaises(SystemExit):
            pst.load_report("/tmp/nonexistent_report_xyzzy_42.csv")

    def test_unsupported_format_exits(self):
        with tempfile.NamedTemporaryFile(suffix=".xlsx", delete=False) as fh:
            fh.write(b"dummy")
            fh.flush()
            with self.assertRaises(SystemExit):
                pst.load_report(fh.name)
        os.unlink(fh.name)

    def test_csv_json_round_trip(self):
        """Write CSV, read back, verify key columns match."""
        df = _sample_dataframe()
        with tempfile.TemporaryDirectory() as tmpdir:
            csv_path = Path(tmpdir) / "roundtrip.csv"
            pst.output_csv(df, csv_path)
            loaded = pst.load_report(str(csv_path))
            self.assertEqual(len(loaded), 2)
            self.assertEqual(
                loaded.iloc[0]["performance_score"],
                df.iloc[0]["performance_score"],
            )


if __name__ == "__main__":
    unittest.main()
