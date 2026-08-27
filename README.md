# chat_history

Queryable chat history for Agent Zero with an optional infinite main chat,
backed by an embedded Postgres (pg0) — no extra containers or services.

## Features

- **Dual-write replica**: every conversation — the main chat, subagents,
  parallel workers, background jobs — is mirrored into an embedded Postgres
  instance after each monologue (plus a periodic resync safety net).
  Because writes land before compaction and inserts are idempotent by
  message id, the store keeps the full raw record even as live chats are
  compacted or reset. JSON chat files remain the source of truth; the
  plugin is additive and can be disabled at any time with zero data loss.
- **Search tools**:
  - `chat_list` — all contexts with message counts and live/running flags
  - `chat_inspect` — tail any context's messages (watch a subagent work)
  - `chat_search` — full-text search across all conversations AND archived
    compaction summaries, optional context filter and time window;
    `mode: "semantic"` adds vector search when an embedding model is
    configured
- **Embeddings without configuration**: uses the embedding model configured
  in Agent Zero's model settings. Model changes are detected and vectors
  are re-embedded automatically. No embedding provider configured →
  semantic mode falls back to full-text search.
- **Continuous Mode (optional, off by default)**: one infinite primary
  chat. At the active Agent Zero preset's `ctx_length * ctx_history` budget
  (reasoning tokens included), the oldest `ctx_length * evict_ratio` of
  complete turns is summarized and removed from native prompt history. The
  raw prompt-message segment and summary are archived in Postgres while the recent
  tail remains intact. The deck (oldest→newest, token-budgeted) is injected
  into every primary-chat turn. Each entry summarizes only raw turns since
  the previous entry — no summary-of-summary compounding. Each summary has
  a provider-enforced output ceiling of 5% of the evicted segment's estimated
  tokens (for example, 5,000 output tokens for a 100,000-token segment). The
  stock synthetic initial assistant
  greeting is suppressed, so a new conversation begins with the first real
  user message. With the mode off, stock Agent Zero auto-compaction runs
  untouched.
- **One-time import**: existing `usr/chats` JSON history is imported on
  first run; a legacy standalone continuous-chat deck file is imported
  idempotently.
- **Export**: `export_context()` writes any context to readable JSONL under
  `usr/exports/chat_history/`.

## Storage modes

- **replica** (default): JSON chat files are the source of truth; the DB
  is an additive copy. Disable the plugin anytime — zero behavior change.
- **authoritative**: the DB is the system of record. After every turn the
  context is serialized with the stock serializer (the exact, lossless
  blob `chat.json` holds — history objects, log entries, colors, metadata)
  into `context_snapshots`, along with the per-message transcript files.
  At startup, missing or stale `usr/chats` files are rebuilt from the DB
  **before** the stock chat loader runs. The live UI (streaming, colors,
  transcript view) always reads files and live state — nothing about the
  stock experience changes; files simply become a cache the DB can always
  rebuild. After loading, an ID-based reconciliation restores any real user
  history rows missing from the WebUI log (while excluding tool results,
  summaries, and framework notifications), then persists the repaired
  snapshot. Chats deleted through the UI are detected and dropped from the DB
  after a grace period.

## Status & operator endpoints (loopback)

- `GET /api/plugins/chat_history/status` — DB health, message counts,
  embedding model, Continuous Mode state
- `POST /api/plugins/chat_history/status` with
  `{"action": "enforce_primary_name"}` — deterministically repair the live
  primary name and return the live and target names
- `GET/POST /api/plugins/chat_history/compact` — deck stats / force one
  rolling-compaction cycle (`{"context_id": ...}` optional)

## Configuration

Settings panel (web UI) or env overrides (env wins):

| Setting | Env | Default |
| --- | --- | --- |
| enabled | `CH_ENABLED` | `1` |
| storage_mode | `CH_STORAGE_MODE` | `replica` |
| embed_enabled | `CH_EMBED_ENABLED` | `1` |
| continuous_mode | `CH_CONTINUOUS_MODE` | `0` |
| evict_ratio | `CH_EVICT_RATIO` | `0.3` |
| min_tokens | `CH_MIN_TOKENS` | `2000` |
| deck_injection | `CH_DECK_INJECTION` | `1` |
| deck_max_tokens | `CH_DECK_MAX_TOKENS` | `2000` |
| main_chat_name | `CH_MAIN_CHAT_NAME` | `Main` |
| lock_main_chat_name | `CH_LOCK_MAIN_CHAT_NAME` | `1` |
| pin_main_chat | `CH_PIN_MAIN_CHAT` | `1` |

Deck retention: **every summary is kept forever** — storage is never
auto-trimmed, so raising `deck_max_tokens` (e.g. after switching to a
larger-context model) renders older entries back in. Manual removal only:
`POST /api/plugins/chat_history/compact` with
`{"action": "trim", "keep": 20}` deletes oldest entries beyond the newest
`keep` (`keep: 0` clears the deck).

Continuous Mode stores the canonical primary context id under
`usr/state/chat_history/primary.json`. On first use it migrates a valid
Telegram binding when present, then falls back to a live, unbound root user
context. The primary context id remains valid when its agent profile changes;
Telegram and agent profiles are not owners of chat identity.
The canonical name is enforced after monologues and during idle job-loop
ticks, so both utility-model renames and startup timing repair themselves.

Stock Agent Zero exposes subordinate contexts as selectable child rows beneath
their parent chat. The history mirror reads that persisted parent metadata, so
`chat_list` and the native sidebar agree on which contexts are subagents. A
child row can be opened normally to inspect or steer its conversation.

In Continuous Mode the WebUI sidebar exposes only the canonical `Main` chat as
a top-level row. Its current subordinate contexts remain available as nested
children. Older top-level chats and their child trees stay persisted and
searchable, but do not clutter the sidebar. WebUI requests to create a new chat
select `Main` instead of allocating a stray context; non-WebUI callers remain
protected by the server-side first-message redirect. The stock Home/Dashboard
button deliberately clears chat selection and remains on the dashboard; that
empty selection is not treated as a hidden legacy chat that needs repair.

## Requirements

`psycopg` and `pg0-embedded` (auto-installed via the plugin's
requirements.txt). The pg0 instance is named `chat_history`, stores data
under Agent Zero's persistent `usr/.pg0/instances/chat_history` directory,
and starts automatically. Storage resolution uses Agent Zero's framework path
helper rather than `HOME`, so the standard `/a0/usr` volume is sufficient.
When stock Agent Zero runs as root, the launcher drops only the PostgreSQL
process to the owner of `usr/.pg0`; the Agent Zero process remains unchanged.

The Plugin Hub runs `hooks.py` on install and during updates. Because the
framework virtual environment is part of the container rather than `usr/`, a
startup hook also checks these imports and reinstalls them after a stock
container replacement. No custom Dockerfile is required. The first start on a
fresh image needs package-network access and can take longer while `uv` or
`pip` populates `/opt/venv-a0`.

Install this directory as `/a0/usr/plugins/chat_history/`, enable it, and
restart the UI.

## Removal

Disable the plugin and restart Agent Zero to return to stock JSON chat
persistence. The uninstall hook intentionally retains shared Python packages
and the pg0 database. Export any history you need before manually deleting the
`chat_history` instance.
