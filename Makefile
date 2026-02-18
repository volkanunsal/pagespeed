# Makefile for pagespeed
# Usage: make <target>

VERSION := $(shell python3 -c "import re; print(re.search(r'__version__ = \"(.+?)\"', open('pagespeed_insights_tool.py').read()).group(1))")

.DEFAULT_GOAL := help

.PHONY: help test build clean install check release

help:
	@echo "pagespeed v$(VERSION)"
	@echo ""
	@echo "  make test      Run the test suite"
	@echo "  make build     Build sdist + wheel into dist/"
	@echo "  make clean     Remove build artifacts and caches"
	@echo "  make install   Install package in editable mode"
	@echo "  make check     Import check + version sanity"
	@echo "  make release   Verify, test, tag v$(VERSION), and push to trigger publish workflow"

test:
	uv run --with pytest pytest test_pagespeed_insights_tool.py -v

build: clean
	uv build

clean:
	rm -rf dist/ .venv/ __pycache__ .pytest_cache
	find . -name "*.pyc" -delete

install:
	uv pip install -e .

check:
	uv run python -c "import pagespeed_insights_tool; print('v' + pagespeed_insights_tool.__version__)"

release:
	@echo "Releasing v$(VERSION)..."
	@git rev-parse --abbrev-ref HEAD | grep -qx "main" || (echo "Error: must be on main branch"; exit 1)
	@git diff --quiet && git diff --cached --quiet || (echo "Error: uncommitted changes"; exit 1)
	@git fetch origin main --quiet
	@git status -uno | grep -q "Your branch is up to date" || (echo "Error: branch is not in sync with origin/main — push or pull first"; exit 1)
	@git tag v$(VERSION) 2>/dev/null || (echo "Error: tag v$(VERSION) already exists"; exit 1)
	@echo "Running tests..."
	@uv run --with pytest pytest test_pagespeed_insights_tool.py -v || (git tag -d v$(VERSION); echo "Error: tests failed — tag removed"; exit 1)
	@git push origin v$(VERSION)
	@echo ""
	@echo "Tag v$(VERSION) pushed — publish workflow triggered."
	@echo "Monitor: gh run list --repo volkanunsal/pagespeed --limit 5"
