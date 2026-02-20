# Plan: Add Median Score Summary Card to HTML Report

**Save as:** `16-add-median-score-card.md`
**Branch:** `017-add-median-score-card`

## Context

The HTML report summary cards currently show Average, Best, and Worst performance scores. Adding a Median score card gives a more robust central-tendency measure — one that isn't skewed by a single outlier URL — alongside the existing stats.

## Changes

### `pagespeed_insights_tool.py`

**1. Compute median in summary stats** (after line 1575, inside `generate_html_report()`):

```python
# existing
avg_score    = scores.mean()   if len(scores) > 0 else 0
best_score   = scores.max()    if len(scores) > 0 else 0
worst_score  = scores.min()    if len(scores) > 0 else 0
# add
median_score = scores.median() if len(scores) > 0 else 0
```

**2. Add Median card to the HTML cards section** (line ~1791, after the Average Score card):

```html
<div class="card">
  <div class="value {score_class(median_score)}">{median_score:.0f}</div>
  <div class="label">Median Score</div>
</div>
```

Insert between the Average Score card and the Best Score card so the natural reading order is:
`URLs Analyzed → Average Score → Median Score → Best Score → Worst Score → (Errors)`

### `test_pagespeed_insights_tool.py`

Add one test to `TestGenerateHtmlReport` (after `test_score_color_classes`, around line 370):

```python
def test_median_score_card_present(self):
    # _sample_dataframe has scores 92 and 98 → median = 95
    html = pst.generate_html_report(self.dataframe)
    self.assertIn("Median Score", html)
    self.assertIn("95", html)
```

## Critical files

- `pagespeed_insights_tool.py` — lines 1571–1576 (stats block) and 1789–1794 (cards HTML)
- `test_pagespeed_insights_tool.py` — `TestGenerateHtmlReport` class (~line 351)

## Verification

```bash
uv run pytest test_pagespeed_insights_tool.py -v -k "TestGenerateHtmlReport"
uv run pytest test_pagespeed_insights_tool.py -v
```

Optionally open a generated report to visually confirm the Median Score card renders correctly.
