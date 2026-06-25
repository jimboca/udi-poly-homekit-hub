# udi-poly-homekit-hub — lint/test, PG3 release artifacts (tag + per-track zips), and bounded ws_debug_client smoke checks.
#
# Quick tests:
#   make test / make test-unit / make test-integration
# WebSocket smoke (hub on WS_HOST:WS_PORT):
#   make ws-smoke WS_HOST=127.0.0.1 WS_PORT=8163
# Optional hub Custom Param ws_token:
#   make ws-hello WS_EXTRA='--token your-secret'
#
# If accessories show live data but ws_* / integration tests look empty, restart the plugin node on IoX/PG3
# so the hub reloads pairings before re-running make/pytest.
#
# PG3 release flow (clean tree; not detached HEAD):
#   1. Bump nodes/__init__.py VERSION; commit.
#   2. `make release`     — tag v<VERSION> and push current branch + tag.
#                           Then in PG3 UI, edit the plugin and set Version to that exact VERSION.
#   3. `make beta`        — push HEAD to the `beta` branch (reference) and build $(NAME)-beta-<VERSION>.zip.
#                           Then in PG3 UI, edit the plugin and set Version to that exact VERSION.
#   4. `make production`  — push HEAD to the `production` branch (reference) and build $(NAME)-production-<VERSION>.zip.
# The track-specific zip files are the actual deliverables uploaded to PG3.

PYTHON ?= python3
PYTEST ?= $(PYTHON) -m pytest
NAME = HomeKitHub
GIT_REMOTE ?= origin
# Reference branches pushed alongside each per-track zip build.
BRANCH_BETA ?= beta
BRANCH_PRODUCTION ?= production
XML_FILES = profile/*/*.xml

WS_HOST ?= 127.0.0.1
WS_PORT ?= 8163
# Extra args for every ws_debug_client invocation (e.g. --token …).
WS_EXTRA ?=

WS := $(PYTHON) ws_debug_client.py --host $(WS_HOST) --port $(WS_PORT) $(WS_EXTRA)

# apt: sudo apt-get install libxml2-utils libxml2-dev
check: xml-check

xml-check:
	xmllint --noout $(XML_FILES)

lint:
	$(PYTHON) -m ruff check .

format-check:
	$(PYTHON) -m ruff format --check .

black-check:
	$(PYTHON) -m black --check .

test:
	$(PYTEST) -q

test-unit:
	$(PYTEST) -q -m "not integration"

test-integration:
	HOMEKIT_WS_HOST=$(WS_HOST) HOMEKIT_WS_PORT=$(WS_PORT) HOMEKIT_WS_INTEGRATION=1 $(PYTEST) tests/test_ws_live.py -v -m integration

help:
	@echo "Quality"
	@echo "  make check / xml-check   Validate profile XML"
	@echo "  make lint / format-check / black-check"
	@echo "  make test                Full pytest suite"
	@echo "  make test-unit           Exclude live WebSocket integration tests"
	@echo "  make test-integration    Live hub tests (HOMEKIT_WS_* / hub)"
	@echo ""
	@echo "PG3 release (clean tree; not detached HEAD)"
	@echo "  make release             Tag v\$$VERSION and push current branch + tag"
	@echo "  make beta                Push HEAD -> $(GIT_REMOTE)/$(BRANCH_BETA) and build $(NAME)-$(BRANCH_BETA)-\$$VERSION.zip"
	@echo "  make production          Push HEAD -> $(GIT_REMOTE)/$(BRANCH_PRODUCTION) and build $(NAME)-$(BRANCH_PRODUCTION)-\$$VERSION.zip"
	@echo "                           After make release / make beta, edit plugin in PG3 UI and set Version to \$$VERSION"
	@echo "  make zip                 Ad-hoc local $(NAME).zip (no version suffix)"
	@echo ""
	@echo "WebSocket smoke (bounded via --max-messages / --oneshot)"
	@echo "  make ws-smoke            All ws-* targets"
	@echo "  make ws-hello … ws-snapshot-all, ws-raw"
	@echo ""
	@echo "Variables: PYTHON WS_HOST WS_PORT WS_EXTRA GIT_REMOTE BRANCH_BETA BRANCH_PRODUCTION"

clean:
	$(PYTHON) -c "import pathlib, shutil; r = pathlib.Path('.'); [shutil.rmtree(p, ignore_errors=True) for p in r.rglob('__pycache__') if p.is_dir()]; shutil.rmtree('.pytest_cache', ignore_errors=True); shutil.rmtree('.ruff_cache', ignore_errors=True)"
	rm -f $(NAME)*.zip

# Ad-hoc local archive (no version suffix). For PG3 uploads, prefer `make beta` / `make production`.
zip:
	rm -f $(NAME).zip
	zip -x@zip_exclude.lst -r $(NAME).zip *

# Push current HEAD to $(GIT_REMOTE)/$(BRANCH_BETA) (reference) and build $(NAME)-$(BRANCH_BETA)-<VERSION>.zip
# for upload to PG3. Requires clean tree; not detached HEAD.
beta:
	@set -e; \
	ROOT=$$(pwd); \
	VERSION=$$(sed -n 's/^VERSION = "\([^"]*\)"$$/\1/p' "$$ROOT/nodes/__init__.py"); \
	test -n "$$VERSION" || { echo "Could not parse VERSION from $$ROOT/nodes/__init__.py"; exit 1; }; \
	test -z "$$(git -C "$$ROOT" status --porcelain)" || { \
		echo "Working tree is not clean. Commit or stash before make beta."; \
		git -C "$$ROOT" status --short; \
		exit 1; \
	}; \
	BRANCH=$$(git -C "$$ROOT" rev-parse --abbrev-ref HEAD); \
	if [ "$$BRANCH" = "HEAD" ]; then \
		echo "ERROR: detached HEAD. Checkout a branch, then run make beta."; \
		exit 1; \
	fi; \
	REPO=$$(git -C "$$ROOT" rev-parse --show-toplevel); \
	git -C "$$ROOT" push "$(GIT_REMOTE)" HEAD:"$(BRANCH_BETA)"; \
	echo "Repository: $$REPO"; \
	echo "Branch: $(BRANCH_BETA)"; \
	echo "Pushed $$(git -C "$$ROOT" rev-parse --short HEAD) to $(GIT_REMOTE)/$(BRANCH_BETA)."; \
	ZIPFILE="$(NAME)-$(BRANCH_BETA)-$$VERSION.zip"; \
	rm -f "$$ZIPFILE"; \
	zip -x@zip_exclude.lst -r "$$ZIPFILE" * >/dev/null; \
	echo "Built $$ROOT/$$ZIPFILE for upload to PG3."; \
	echo "PG3 UI action required: edit this plugin and set Version to $$VERSION."

# Push current HEAD to $(GIT_REMOTE)/$(BRANCH_PRODUCTION) (reference) and build $(NAME)-$(BRANCH_PRODUCTION)-<VERSION>.zip
# for upload to PG3. Requires clean tree; not detached HEAD.
production:
	@set -e; \
	ROOT=$$(pwd); \
	VERSION=$$(sed -n 's/^VERSION = "\([^"]*\)"$$/\1/p' "$$ROOT/nodes/__init__.py"); \
	test -n "$$VERSION" || { echo "Could not parse VERSION from $$ROOT/nodes/__init__.py"; exit 1; }; \
	test -z "$$(git -C "$$ROOT" status --porcelain)" || { \
		echo "Working tree is not clean. Commit or stash before make production."; \
		git -C "$$ROOT" status --short; \
		exit 1; \
	}; \
	BRANCH=$$(git -C "$$ROOT" rev-parse --abbrev-ref HEAD); \
	if [ "$$BRANCH" = "HEAD" ]; then \
		echo "ERROR: detached HEAD. Checkout a branch, then run make production."; \
		exit 1; \
	fi; \
	REPO=$$(git -C "$$ROOT" rev-parse --show-toplevel); \
	git -C "$$ROOT" push "$(GIT_REMOTE)" HEAD:"$(BRANCH_PRODUCTION)"; \
	echo "Repository: $$REPO"; \
	echo "Branch: $(BRANCH_PRODUCTION)"; \
	echo "Pushed $$(git -C "$$ROOT" rev-parse --short HEAD) to $(GIT_REMOTE)/$(BRANCH_PRODUCTION)."; \
	ZIPFILE="$(NAME)-$(BRANCH_PRODUCTION)-$$VERSION.zip"; \
	rm -f "$$ZIPFILE"; \
	zip -x@zip_exclude.lst -r "$$ZIPFILE" * >/dev/null; \
	echo "Built $$ROOT/$$ZIPFILE for upload to PG3."

# Tag the current HEAD as v<VERSION> and push the current branch + tag to $(GIT_REMOTE).
# Version = nodes/__init__.py VERSION (canonical). Track-specific zips are built by `make beta` / `make production`.
# Run from this directory, or: make -C /path/to/udi-poly-homekit-hub release
# Requires clean git working tree and a checked-out branch (not detached HEAD).
release:
	@set -e; \
	ROOT=$$(pwd); \
	VERSION=$$(sed -n 's/^VERSION = "\([^"]*\)"$$/\1/p' "$$ROOT/nodes/__init__.py"); \
	test -n "$$VERSION" || { echo "Could not parse VERSION from $$ROOT/nodes/__init__.py"; exit 1; }; \
	test -z "$$(git -C "$$ROOT" status --porcelain)" || { \
		echo "Working tree is not clean. Commit or stash before make release."; \
		git -C "$$ROOT" status --short; \
		exit 1; \
	}; \
	BRANCH=$$(git -C "$$ROOT" rev-parse --abbrev-ref HEAD); \
	if [ "$$BRANCH" = "HEAD" ]; then \
		echo "ERROR: detached HEAD. Checkout your release branch (e.g. main), then run make release."; \
		exit 1; \
	fi; \
	if git -C "$$ROOT" rev-parse -q --verify "refs/tags/v$$VERSION" >/dev/null 2>&1; then \
		echo "Tag v$$VERSION already exists. Delete: git -C \"$$ROOT\" tag -d v$$VERSION"; \
		exit 1; \
	fi; \
	git -C "$$ROOT" tag -a "v$$VERSION" -m "Release $$VERSION"; \
	echo "Created annotated tag v$$VERSION."; \
	git -C "$$ROOT" push "$(GIT_REMOTE)" "$$BRANCH" "v$$VERSION"; \
	echo "Pushed $$BRANCH and v$$VERSION to $(GIT_REMOTE)."; \
	echo "PG3 UI action required: edit this plugin and set Version to $$VERSION."

# --- ws_debug_client exercises (exit after N inbound frames; no infinite monitor) ---

ws-hello:
	$(WS) --oneshot

ws-list:
	$(WS) --command '{"version":"1","action":"list_devices"}' --max-messages 2

ws-get:
	$(WS) --command '{"version":"1","action":"get","device_id":"00:00:00:00:00:00","characteristic":"ON"}' --max-messages 3

ws-subscribe:
	$(WS) --command '{"version":"1","action":"subscribe","device_id":"00:00:00:00:00:00","aid":1,"iid":1}' --max-messages 3

ws-unsubscribe:
	$(WS) --command '{"version":"1","action":"unsubscribe","device_id":"00:00:00:00:00:00","aid":1,"iid":1}' --max-messages 3

ws-snapshot-device:
	$(WS) --snapshot-device-id 00:00:00:00:00:00 --max-messages 4

ws-snapshot-all:
	$(WS) --snapshot-all --max-messages 25

ws-raw:
	$(WS) --raw --oneshot

ws-smoke: ws-hello ws-list ws-get ws-subscribe ws-unsubscribe ws-snapshot-device ws-snapshot-all ws-raw
	@echo "ws-smoke: finished ($(WS_HOST):$(WS_PORT))"

.PHONY: check xml-check lint format-check black-check test test-unit test-integration help clean zip beta production release \
	ws-smoke ws-hello ws-list ws-get ws-subscribe ws-unsubscribe ws-snapshot-device ws-snapshot-all ws-raw
