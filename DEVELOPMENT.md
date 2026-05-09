# Development

Setup and tests:

```bash
pip install -r requirements.txt -r requirements-dev.txt
pytest -q
```

Lint is checked in **GitHub Actions** with [Ruff](https://docs.astral.sh/ruff/) (pinned in the workflow). Locally: `pip install ruff==0.8.6 && ruff check .` (optional).

## Releases

Polyglot installs use a **git URL + branch**. This repo uses two remote branches: **`beta`** (pre-release) and **`production`** (stable). On a **branch** (not detached `HEAD`) with a **clean** git tree:

- **`make beta`** — pushes **current `HEAD`** to **`origin/beta`** (override remote: **`GIT_REMOTE=myfork`**; override branch name: **`BRANCH_BETA=...`**).
- **`make production`** — same for **`origin/production`** (**`BRANCH_PRODUCTION=...`**).
- **`make release`** — parses **`VERSION`** from **`nodes/__init__.py`**, creates annotated **`v`<version>**, **`git push`**es the current branch, **`v`<version>**, and **`HEAD` → `production`**, then writes **`release-pg3-store.txt`** (versions and git branch hints for the PG3 store). Does **not** build a zip.

**`make zip`** remains for an optional **local `HomeKitHub.zip`** (legacy / manual upload); primary delivery is the branches above.

## Layout

- `homekit-poly.py` — entry point
- `homekit_hub/bridge.py` — aiohomekit + WebSocket (default port **8163**), multi-slot pairing
- `nodes/Controller.py` — PG3 lifecycle and custom params/data
- `PROTOCOL.md` — JSON message contract (`version` **1**)
