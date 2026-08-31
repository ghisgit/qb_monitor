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
- `organize` is an optional section (absent → disabled). When `enabled: true`: `tags` non-empty, `library.movies_dir|tv_dir|fallback_dir` non-empty strings, `on_exists` ∈ {skip, overwrite}, `on_match_failure` ∈ {fallback, fail}, `min_file_size_mb` ≥ 0, `tmdb_api_key` non-empty, `ai_retries` ≥ 0, `dsh.request_timeout_seconds` > 0, DeepSeek key via `dsh.api_key` or `DEEPSEEK_API_KEY` env — else `ValueError` at startup.
- Logging uses a nested `logging` section (not flat `logfile`/`debug_mode`).

## Architecture

- Layout: `core/` holds infrastructure (`breaker.py`, `client.py`, `logger.py`, `models.py`, `orchestrator.py`); `handlers/` holds all handlers, with organize support in `handlers/organize/` (`handler.py`, `matcher.py`, `index.py`, `naming.py`). Imports are root-flat absolute (`from core.client import ...`); `handlers/__init__.py` and `handlers/organize/__init__.py` must stay **empty** — `matcher.py` imports `deepseek_harness` at module top, so any eager re-export in a package `__init__` would break Windows dev / lazy loading.
- Tag-driven dispatch: trigger tags are derived dynamically from the handler registry; adding a new trigger tag needs only `register_handler` (zero polling changes).
- Producer-consumer via `threading + queue.Queue`. Orchestrator polls qBittorrent; torrents with metadata, no `processing` tag and any registered trigger tag are batch-tagged `processing` and enqueued uniformly (trigger tags are NOT removed during polling).
- Worker threads dispatch: dict tasks route through `_batch_dispatcher` by `type` key; torrent tasks run the first matching trigger-tag handler (registration order), then the orchestrator removes the trigger tag on success (kept on failure → retried next cycle), then runs the post chain if the tag opted in via `enable_post_chain=True`.
- Registrations in `main.py`: `added` / `completed` (both `enable_post_chain=True`), batch `monitoring` via `register_batch_handler`, per-action post handlers `CategoryTagHandler.handle_added` / `.handle_completed` (tags torrents by qBittorrent Category via case-insensitive `re.search` regex rules; each torrent has at most one category and all matching patterns' tags are merged & deduped; post handlers may be scoped to trigger tags via `register_post_handler(fn, tags=...)`), and one `organize` tag handler per entry in `organize.tags` (no post chain).
- OrganizeHandler / DeepSeekMatcher: DSH agent (via `deepseek-harness-sdk`) does recognition + TMDB matching and returns a validated JSON plan; Python deterministically hardlinks per-file with copy fallback into Jellyfin-structured paths (`handlers/organize/naming.py`). The prompt uses python3 urllib (image has no curl) and tells the agent to fall back to reading `organize.tmdb_api_key` from config.yaml when `TMDB_API_KEY` is absent — DSH bash tools scrub env vars, so harness `env` injection may not reach the agent's shell. `organize.tmdb_proxy` (optional) injects a ready-made urllib ProxyHandler recipe into the prompt when api.themoviedb.org is firewalled; without it every fresh session re-discovers the proxy at great cost. AI turns run concurrently on the single reused `DeepSeekHarness` runtime up to `BoundedSemaphore(max_concurrent_sessions)` (default 1) — an attempt holds its slot across the initial turn AND the corrective follow-up; when all slots are busy `_match_attempt` raises `TaskDeferredError` (defined in `core/models.py`, NOT a `MatchError` subclass so it never triggers fallback mirroring) — `main.py`'s worker logs it as INFO, trigger tag stays, the torrent re-queues next poll cycle. `TaskDeferredError` is re-raised past the `ai_retries` loop in `matcher.match()`. The runtime's lazy first start is serialized by a dedicated `_start_lock` (SDK start/initialize are not thread-safe; without it concurrent slots race the spawn). Non-JSON first replies get one corrective follow-up turn on the SAME session (preserves TMDB context); per-torrent fresh `session_id`; matcher closed on shutdown.
- `OrganizeIndex` (`handlers/organize/index.py`, pure stdlib, `state/organize_index.json`, gitignored): persists per-torrent plan after a successful organize (fingerprint = sorted relative paths of planned+placed files, stored dests). A re-triggered torrent whose fingerprint is unchanged reuses the recorded plan+dests and **skips the AI call entirely** (idempotent `_place_file` re-applies missing dests); a changed fingerprint re-runs AI and refreshes the index. Fallback mirrors (`on_match_failure: fallback`) are **not** indexed so re-tagging retries AI. Corrupt/missing index = treated as empty (full flow once, then rebuilt); force re-match by deleting the hash entry from the file (see README 3.1).
- MonitorHandler tracks per-hash stall timestamps in-memory; lost on restart. Logging is **event-driven, not per-cycle** (no debug spam each poll): it logs only state transitions — `Tracking`/`Stop tracking` (DEBUG), `Stalled` (WARNING, fires once per stall to avoid repeat alerts), `Recovered`/`Demoted` (INFO) — plus a single per-cycle aggregate summary line at DEBUG that is **emitted only when something changed** (silent otherwise). Demotion still only runs when `downloading_count > demotion_threshold`.

## Quirks / gotchas

- 190 tests across 12 test files in `tests/`; run with `uv run pytest`.
- Python requirement: `>=3.14` (per `pyproject.toml` and `.python-version`).
- Ruff: `line-length = 120`, `quote-style = "double"`, target `py314`.
- Pyright: `venvPath = "."`, `venv = ".venv"`, `typeCheckingMode = "basic"`.
- qBittorrent joins torrent tags with `", "` (comma+space) in `torrents_info` (e.g. `"added, processing"`); tag parsing must strip each item — `orchestrator._parse_tags()` is the single parsing point (a plain `split(",")` makes the `processing` guard never match and every cycle re-queues a processing torrent).
- Logging has a `FATAL` level (value 60), custom `QBLogger`, JSON-structured format in production, thread-local `ContextFilter` for contextual fields, and `SensitiveDataFilter` for secrets.
- Docker uses `python:3.14-slim-bookworm` with uv; run `docker build -t qb-monitor .` to build.
- All qBittorrent API calls go through `_request()` with retry logic (exponential backoff, up to 30s cap) and circuit breaker protection.
- Autorun for added/completed tags is configured automatically via API on startup — no manual qBittorrent config needed.
- `deepseek-harness-sdk` is marked `sys_platform != 'win32'` (runtime-bin has no Windows wheel) — `main.py` imports it lazily inside the enabled-`organize` branch; Windows dev keeps `organize.enabled: false`.
- `uv` cache may need `UV_CACHE_DIR` (e.g. `/tmp/uvcache`) when the default `/data/.cache/uv` is not writable.
