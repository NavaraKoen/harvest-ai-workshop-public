# Harvest AI Workshop — Music Recommendations

A durable **Temporal music recommendation loop**: Spotify and learned preferences feed
an LLM, a song is posted to Slack every minute, and user feedback steers future picks.

### Recommendation behavior: double down on negative feedback

The recommender is deliberately contrarian. **The more negative the feedback, the
more it leans into the musical qualities being criticized.** Mild dislike gives those
qualities a small boost; strong dislike or repeated complaints makes them dominant.
For example, “too much guitar” leads to more guitar-heavy songs, while “I absolutely
hate this noisy metal” pushes toward even noisier, heavier metal. Vague dislikes use
the referenced song's style. Positive feedback reinforces qualities normally.
Initial preferences provide the baseline; negative feedback is not an exclusion filter.
This policy applies to preference updates, Spotify searches, and final track selection.

## Architecture

```
frontend/           Recommendations, feedback, current preferences, and LLM logs
api/                FastAPI proxy → Temporal Client
temporal_app/
  workflows/music.py  MusicWorkflow (one-minute loop)
  activities/music.py Spotify, LLM, preference updates, Slack
llm/                LLM abstraction (Ollama / OpenAI / Anthropic)
config/tools.py     Original travel-chat tool registry
```

### Workflow lifecycle

```
start / reconnect (one workflow per Slack channel)
  └─ while True:
       feedback → update preferences
       preferences + recent songs → LLM search query
       Spotify API → real track candidates
       candidates + preferences → LLM recommendation
       Slack API → post song
       durable timer → next one-minute tick

Slack thread replies / browser feedback → durable workflow signals
```

The first recommendation runs immediately. Slow cycles do not overlap. Feedback is
processed on the next tick, and includes the song it refers to. The workflow remembers
the last 100 recommendations (also the window for Slack thread feedback) and avoids
recommending those tracks again. Empty search results are skipped until the next tick.
Preferences survive worker restarts and continue-as-new every 100 cycles; recent
history and LLM logs are bounded. **Stop recommendations** ends the loop; a new session
starts with a fresh profile. Reconnecting to a running session keeps its profile.

## Prerequisites

| Tool | Notes |
|------|-------|
| Python 3.11+ | |
| [uv](https://docs.astral.sh/uv/) | `brew install uv` |
| [Temporal CLI](https://docs.temporal.io/cli) | `brew install temporal` |
| [Ollama](https://ollama.com) | for local LLM |
| mistral model | `ollama pull mistral` |

## Quick start

```bash
# 1. Clone / open the project, then install dependencies
uv sync

# 2. Copy and edit env (configure Spotify and Slack below)
cp .env.example .env

# 3. Start Temporal dev server (keep running)
temporal server start-dev

# 4. Start the worker (keep running in a separate terminal)
uv run worker

# 5. Start the API + frontend (keep running in a separate terminal)
uv run api

# 6. Open in browser
open http://localhost:8000
```

To disable activity retries for new music workflows, start the API with the optional flag:

```bash
uv run api --no-retries
```

Music activities make up to three attempts by default. The flag sets the maximum
attempts to one. Exhausted failures appear in the UI and Temporal's workflow history.

## Spotify and Slack setup

1. Create a Spotify developer app and set `SPOTIFY_CLIENT_ID`,
   `SPOTIFY_CLIENT_SECRET`, and optionally `SPOTIFY_MARKET` in `.env`. This uses
   client credentials to search the catalog, not a user's private listening history.
2. Create a Slack app with a bot, give it `chat:write`, install it into your workspace,
   and invite it to the target channel. Set `SLACK_BOT_TOKEN` and `SLACK_CHANNEL_ID`
   (the channel ID, such as `C0123456789`, not its name).
3. To collect feedback from Slack, enable Events API and point its Request URL to
   `https://<your-public-api-host>/api/slack/events` (use an HTTPS tunnel for local
   development). Set the app's `SLACK_SIGNING_SECRET` in `.env` before URL verification.
4. Subscribe to bot event `message.channels` for public channels with
   `channels:history`, or `message.groups` for private channels with `groups:history`.
   Reinstall the app after changing scopes. Reply **in a recommendation's thread**;
   ordinary channel messages and bot messages are ignored. Retried events are deduplicated.
5. Start the server and worker, open the browser, and click **Start / reconnect**.
   Browser feedback works without a public Slack callback URL.

The local browser/API is a workshop interface without authentication. Slack callbacks
are signature-verified. When tunneling, expose only `/api/slack/events`.

You can also seed a new session with preferences:

```sh
curl -X POST http://localhost:8000/api/chat/start \
  -H 'Content-Type: application/json' \
  -d '{"preferences":"Indie rock and mellow electronic; no metal"}'
```

The `/api/chat/*` paths are retained for the browser interface. Start is idempotent
while a channel's workflow is running. Posting to Slack uses a stable `client_msg_id`
across retries; as with external API side effects, delivery is not an exactly-once guarantee.

## Tests

Tests use fake Spotify/Slack/LLM responses and a temporary local Temporal dev server
(`temporal` on PATH). The timer integration test takes about one minute.

```bash
uv run python -m unittest discover -s tests -v
```

## Switching to OpenAI (or any OpenAI-compatible API)

Edit `.env`:

```env
LLM_PROVIDER=openai
OPENAI_API_KEY=sk-...
OPENAI_MODEL=gpt-4o-mini
# Optional — point at a local proxy like LM Studio:
# OPENAI_BASE_URL=http://localhost:1234/v1
```

## Switching to Anthropic

Edit `.env`:

```env
LLM_PROVIDER=anthropic
ANTHROPIC_API_KEY=sk-ant-...
ANTHROPIC_MODEL=claude-sonnet-4-20250514
ANTHROPIC_MAX_TOKENS=4096
```

## Original chat workflow

The original `ChatWorkflow` and travel-service examples remain registered for existing
executions. New browser sessions start `MusicWorkflow`.
