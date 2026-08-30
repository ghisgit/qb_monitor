# QB Monitor — Agent Guide

## Quick start

```powershell
uv sync --group dev    # install deps + dev deps
uv run python main.py # requires config.yaml (see template_config.yaml)
```

## Commands

| Task | Command |
|------|---------|
| Lint | `uv run ruff check .` |
| Format | `uv run ruff format .` |
| Typecheck | `uv run pyright` |
| Test | `uv run pytest` |
| Sync deps | `uv sync` |
| Add dep | `uv add <pkg>` |
| Add dev dep | `uv add --dev <pkg>` |

Run order: `ruff check .` → `ruff format . --check` → `pyright` → `pytest`.

## Package management

- **uv only** (not pip). No `requirements.txt`.
- `uv.lock` is tracked in git; update with `uv lock` when adding/changing deps.

## Config

- Copy `template_config.yaml` → `config.yaml` (gitignored) before running.
- Config schema validated at startup in `main.py` — section top-level keys are mandatory; `category_tags` is an optional section (when present: mapping of action `added`/`completed` → non-empty mapping of category regex → non-empty tag string or tag list; patterns must compile).
- Logging uses a nested `logging` section (not flat `logfile`/`debug_mode`).

## Architecture

- Tag-driven dispatch: trigger tags are derived dynamically from the handler registry; adding a new trigger tag needs only `register_handler` (zero polling changes).
- Producer-consumer via `threading + queue.Queue`. Orchestrator polls qBittorrent; torrents with metadata, no `processing` tag and any registered trigger tag are batch-tagged `processing` and enqueued uniformly (trigger tags are NOT removed during polling).
- Worker threads dispatch: dict tasks route through `_batch_dispatcher` by `type` key; torrent tasks run the first matching trigger-tag handler (registration order), then the orchestrator removes the trigger tag on success (kept on failure → retried next cycle), then runs the post chain if the tag opted in via `enable_post_chain=True`.
- Four registrations in `main.py`: `added` / `completed` (both `enable_post_chain=True`), batch `monitoring` via `register_batch_handler`, and per-action post handlers `CategoryTagHandler.handle_added` / `.handle_completed` (tags torrents by qBittorrent Category via case-insensitive `re.search` regex rules; each torrent has at most one category and all matching patterns' tags are merged & deduped; post handlers may be scoped to trigger tags via `register_post_handler(fn, tags=...)`).
- MonitorHandler tracks per-hash stall timestamps in-memory; lost on restart.

## Quirks / gotchas

- 82 tests across 7 test files in `tests/`; run with `uv run pytest`.
- Python requirement: `>=3.14` (per `pyproject.toml` and `.python-version`).
- Ruff: `line-length = 120`, `quote-style = "double"`, target `py314`.
- Pyright: `venvPath = "."`, `venv = ".venv"`, `typeCheckingMode = "basic"`.
- Logging has a `FATAL` level (value 60), custom `QBLogger`, JSON-structured format in production, thread-local `ContextFilter` for contextual fields, and `SensitiveDataFilter` for secrets.
- Docker uses `python:3.14-slim-bookworm` with uv; run `docker build -t qb-monitor .` to build.
- All qBittorrent API calls go through `_request()` with retry logic (exponential backoff, up to 30s cap) and circuit breaker protection.
- Autorun for added/completed tags is configured automatically via API on startup — no manual qBittorrent config needed.
