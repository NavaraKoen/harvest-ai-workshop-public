# Harvest AI Workshop — Temporal Chat

A full-stack Python project wiring a **Temporal workflow** to an **LLM-backed chat UI**.

## Architecture

```
frontend/           Static chat UI (polling FastAPI)
api/                FastAPI proxy → Temporal Client
temporal_app/
  workflows/        ChatWorkflow  (while-true loop)
  activities/       propose_next_action, execute_tool
llm/                LLM abstraction (Ollama / OpenAI / Anthropic)
config/tools.py     Tool registry
```

### Workflow lifecycle

```
start_workflow()
  └─ while True:
       propose_next_action()   ← LLM decides next step
         ├─ ask_input          → wait for send_user_input signal
         ├─ ask_confirmation   → wait for send_confirmation signal
         │                        ├─ confirmed → execute_tool()
         │                        └─ declined  → add note to history
         ├─ execute_tool       → execute_tool() immediately
         └─ final_message      → end chat
```

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

# 2. Copy and edit env
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

To disable activity retries for new chat workflows, start the API with the optional flag:

```bash
uv run api --no-retries
```

Without the flag, activities use Temporal's default retry policy. The flag sets the maximum
attempts to one, which means zero retries.

## Slides

The Slidev presentation lives in `slides/` and uses pnpm:

```bash
pnpm --dir slides install
pnpm --dir slides dev
```

Build the deck with `pnpm --dir slides build` or export it with `pnpm --dir slides export`.

## Temporal-less failure demo

The standalone [`temporal_less_demo/`](temporal_less_demo/) shows the same kind of LLM
failure without Temporal or automatic retries:

```bash
uv run python temporal_less_demo/app.py --scenario api-error
uv run python temporal_less_demo/app.py --scenario invalid-json
uv run python temporal_less_demo/app.py --scenario success
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

## Adding tools

Edit [config/tools.py](config/tools.py): add a `ToolDefinition` entry with a `handler` callable.  
Set `require_confirmation=True` to make the workflow ask the user before running the tool.
