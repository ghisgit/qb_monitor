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
- Config schema validated at startup in `main.py:13` — section top-level keys are mandatory.
- Logging uses a nested `logging` section (not flat `logfile`/`debug_mode`).

## Architecture

- Tag-based state machine: `added → processing → (tag removed)`, same for `completed`.
- Producer-consumer via `threading + queue.Queue`. Orchestrator polls qBittorrent, enqueues tasks; worker threads dispatch via registered handlers.
- Three handlers registered in `main.py:87-89`: `added`, `completed`, `monitoring` (batch dict, not single torrent).
- MonitorHandler tracks per-hash stall timestamps in-memory; lost on restart.

## Quirks / gotchas

- No test directory exists yet; `tests/` is in `.gitignore`.
- Python requirement: `>=3.14` (per `pyproject.toml` and `.python-version`).
- Ruff: `line-length = 120`, `quote-style = "double"`, target `py314`.
- Pyright: `venvPath = "."`, `venv = ".venv"`, `typeCheckingMode = "basic"`.
- Logging has a `FATAL` level (value 60), custom `QBLogger`, JSON-structured format in production, thread-local `ContextFilter` for contextual fields, and `SensitiveDataFilter` for secrets.
- Shell scripts in `scripts/` are **not** run inside Docker; they run on the qBittorrent host as external program hooks.
- Docker uses `python:3.14-slim-bookworm` with uv; run `docker build -t qb-monitor .` to build.
- All qBittorrent API calls go through `@retry` decorator (exponential backoff, up to 30s cap).
