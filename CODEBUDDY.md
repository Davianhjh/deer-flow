# CODEBUDDY.md This file provides guidance to CodeBuddy when working with code in this repository.

## Commands

### Project Root (full application)

| Command | Purpose |
|---------|---------|
| `make setup` | Interactive setup wizard — chooses LLM provider, web search, sandbox preferences. Generates `config.yaml` and writes secrets to `.env`. |
| `make doctor` | Verify setup correctness and get actionable fix hints. Run anytime after config changes. |
| `make config` | Copy `config.example.yaml` to `config.yaml` (aborts if config already exists). |
| `make config-upgrade` | Merge new fields from `config.example.yaml` into existing `config.yaml`. |
| `make check` | Verify Node.js 22+, pnpm, uv, and nginx are installed. |
| `make install` | Install all dependencies: `uv sync` for backend, `pnpm install` for frontend, then pre-commit hooks. |
| `make dev` | Start all services locally with hot-reload: Gateway (8001), Frontend (3000), Nginx (2026). Config preflight enforced. |
| `make start` | Start production services locally (pre-built frontend, no hot-reload). |
| `make stop` | Stop all running local services. |
| `make clean` | Stop services and remove `.deer-flow`, `.langgraph_api`, and log files. |
| `make setup-sandbox` | Pre-pull the sandbox container image for Docker/Container-based sandbox modes. |

### Docker Development

| Command | Purpose |
|---------|---------|
| `make docker-init` | Build Docker images and install all dependencies inside containers (first-time only). |
| `make docker-start` | Start Docker dev environment on port 2026 with hot-reload and source mounts. Auto-detects sandbox mode from `config.yaml`. |
| `make docker-stop` | Stop Docker dev services. |
| `make docker-logs` | View all Docker dev container logs. Subset with `--frontend` or `--gateway`. |
| `make up` | Build images and start all production Docker services. |
| `make down` | Stop and remove production containers. |

### Backend (`cd backend`)

| Command | Purpose |
|---------|---------|
| `make install` | Install Python dependencies via `uv sync`. |
| `make dev` | Run Gateway API with uvicorn and hot-reload on port 8001. |
| `make gateway` | Run Gateway API without hot-reload on port 8001. |
| `make test` | Run all backend tests: `PYTHONPATH=. uv run pytest tests/ -v` |
| `make lint` | Ruff check + format check. |
| `make format` | Ruff auto-fix + format. |
| `PYTHONPATH=. uv run pytest tests/test_<feature>.py -v` | Run a single test file. |

### Frontend (`cd frontend`)

| Command | Purpose |
|---------|---------|
| `pnpm dev` | Dev server with Turbopack on port 3000. |
| `pnpm build` | Production build. |
| `pnpm check` | Run lint + type-check (do this before committing). |
| `pnpm lint` | ESLint only. Use `pnpm lint:fix` for auto-fix. |
| `pnpm test` | Run Vitest unit tests. |
| `pnpm test:e2e` | Run Playwright E2E tests with Chromium. |
| `pnpm typecheck` | TypeScript type check (`tsc --noEmit`). |

## Architecture

DeerFlow is a LangGraph-based **super agent harness** — an open-source platform that orchestrates sub-agents, memory, skills, and sandbox environments to execute complex, multi-step tasks. The codebase is a full-stack monorepo at `deer-flow/` with **backend** (Python/LangGraph), **frontend** (Next.js/TypeScript), and shared **skills** directories.

### Service Topology

Nginx on port **2026** is the unified reverse-proxy entry point. It routes requests to three backends:
- `/api/langgraph/*` → Gateway embedded runtime (port 8001), rewritten to native `/api/*` routers
- `/api/*` (other) → Gateway API (port 8001) — FastAPI REST endpoints
- `/` (non-API) → Frontend Next.js dev server (port 3000)

In Docker dev, an optional **provisioner** (port 8002) starts only for Kubernetes/provisioner sandbox mode. The agent runtime runs inside the Gateway process via `RunManager` + `run_agent()` + `StreamBridge` at `packages/harness/deerflow/runtime/`.

### Harness / App Split (Backend)

The backend enforces a **strict two-layer architecture** with a one-way dependency:
- **Harness** (`backend/packages/harness/deerflow/`): The publishable `deerflow-harness` package. Import prefix `deerflow.*`. Contains all agent logic — orchestration, tools, sandbox, models, MCP, skills, memory, subagents, configuration. This is the framework core.
- **App** (`backend/app/`): Unpublished application code. Import prefix `app.*`. Contains the FastAPI Gateway and IM channel integrations (Feishu, Slack, Telegram, DingTalk, WeChat, WeCom).

**Rule**: App imports deerflow; deerflow MUST NEVER import app. CI enforces this via `test_harness_boundary.py`.

### Lead Agent System

The entry point is `make_lead_agent(config)` registered in `langgraph.json`. It assembles:
- **Dynamic model selection** via `create_chat_model()` supporting thinking toggles, vision, and provider-specific overrides (OpenAI, Anthropic, DeepSeek, vLLM, Ollama, OpenRouter, etc.)
- **Tool assembly** via `get_available_tools()` combining sandbox tools, built-in tools, MCP tools, community tools, and subagent delegation
- **System prompt** with skills injection, memory context, and working-directory guidance

**Runtime configuration** flows through `config.configurable`: `thinking_enabled`, `model_name`, `is_plan_mode`, `subagent_enabled`.

### Middleware Chain (18 middlewares, in order)

Each middleware is a discrete lifecycle hook in the agent loop:
1. **ThreadDataMiddleware** — Creates per-thread isolated directories under `backend/.deer-flow/users/{user_id}/threads/{thread_id}/user-data/{workspace,uploads,outputs}`
2. **UploadsMiddleware** — Injects newly uploaded files into conversation
3. **SandboxMiddleware** — Acquires sandbox, stores `sandbox_id` in state
4. **DanglingToolCallMiddleware** — Fixes interrupted tool-call loops by injecting placeholder ToolMessages
5. **LLMErrorHandlingMiddleware** — Normalizes provider failures into recoverable errors
6. **GuardrailMiddleware** — Optional pre-tool-call authorization via pluggable providers (allowlist, OAP, custom)
7. **SandboxAuditMiddleware** — Security audit logging for shell/file operations
8. **ToolErrorHandlingMiddleware** — Converts tool exceptions into error messages (never aborts the run)
9. **SummarizationMiddleware** — Context reduction when approaching token limits (optional, configurable triggers)
10. **TodoListMiddleware** — Task tracking with `write_todos` tool in plan mode
11. **TokenUsageMiddleware** — Records per-model token counts
12. **TitleMiddleware** — Auto-generates thread title after first exchange
13. **MemoryMiddleware** — Queues conversations for async memory extraction
14. **ViewImageMiddleware** — Injects base64 images for vision-capable models
15. **DeferredToolFilterMiddleware** — Hides deferred tool schemas until tool search is enabled
16. **SubagentLimitMiddleware** — Enforces `MAX_CONCURRENT_SUBAGENTS = 3` by truncating excess tool calls
17. **LoopDetectionMiddleware** — Detects repeated tool-call loops and forces text response
18. **ClarificationMiddleware** — Intercepts `ask_clarification` calls and interrupts via `Command(goto=END)` (must be last)

### Sandbox System

Each thread gets an isolated execution environment with virtual path translation:
- **Agent sees**: `/mnt/user-data/{workspace,uploads,outputs}`, `/mnt/skills`
- **Physical**: `backend/.deer-flow/users/{user_id}/threads/{thread_id}/user-data/...`, `<project_root>/skills/`
- **Providers**: `LocalSandboxProvider` (host filesystem, bash disabled by default) or `AioSandboxProvider` (Docker container isolation)
- **Tools**: `bash`, `ls`, `read_file`, `write_file` (with `append`), `str_replace` (per-`(sandbox_id, path)` serialization), `glob`, `grep`

### Subagent System

The lead agent spawns sub-agents via the `task()` tool. Built-in types: `general-purpose` (full tools minus `task`) and `bash` (command specialist). Custom sub-agents can be defined in `config.yaml` with their own prompts, tool whitelists, skill whitelists, and model overrides. Concurrency is capped at 3 per turn with a 15-minute timeout. Background execution uses dual thread pools (3 scheduler + 3 executor workers) with SSE event reporting.

### Tool Ecosystem

`get_available_tools()` assembles tools from multiple sources: config-defined tools resolved via reflection (`module.path:variable_name`), MCP servers (lazy-init with mtime cache invalidation), built-in tools (`present_files`, `ask_clarification`, `view_image`, `setup_agent`, `update_agent`), community tools (DuckDuckGo search, Jina AI fetch, Tavily, Firecrawl, Serper, Exa, InfoQuest, image search), ACP agent tools, and the subagent `task` tool. Custom agents get `setup_agent` at bootstrap time and `update_agent` for self-modification.

### Memory System

LLM-powered persistent memory across sessions. Stores per-user profile in `{base_dir}/users/{user_id}/memory.json` with facts (category, confidence), context summaries, and history. Custom agent memory lives at `{base_dir}/users/{user_id}/agents/{agent_name}/memory.json`. Updates are debounced (30s default), deduplicated (whitespace-normalized fact comparison), and atomically written. Top facts injected into system prompt via `<memory>` tags. In no-auth mode, `user_id` defaults to `"default"`.

### Skills System

Skills are Markdown-based capability modules under `skills/{public,custom}/` with YAML frontmatter. They are loaded progressively — only injected into the system prompt when the task needs them. The Gateway can install `.skill` archives via `POST /api/skills/install`, accepting standard `version`/`author`/`compatibility` frontmatter. Skills are mounted at `/mnt/skills` inside sandboxes.

### Configuration

- `config.yaml` (project root): Models, tools, sandbox, skills, memory, subagents, channels, summarization, guardrails, loop detection, circuit breaker, database settings. Config versioned — run `make config-upgrade` on schema changes. Environment variable references prefixed with `$`. Auto-reloads on file mtime change.
- `extensions_config.json` (project root): MCP server definitions and per-skill enabled states. Modifiable at runtime via Gateway API.
- Key env vars: `DEER_FLOW_CONFIG_PATH`, `DEER_FLOW_PROJECT_ROOT`, `DEER_FLOW_HOME`, `DEER_FLOW_SKILLS_PATH`, `DEER_FLOW_EXTENSIONS_CONFIG_PATH`.

### Frontend Architecture

Next.js 16 App Router with: `src/app/` (routes — landing, workspace chats), `src/core/` (business logic — threads, API client, artifacts, i18n, memory, settings, skills, MCP), `src/components/` (shadcn-ui primitives, workspace UI, landing, AI elements). Communication with the backend uses `@langchain/langgraph-sdk` through a singleton client (`getAPIClient()`). Thread hooks (`useThreadStream`, `useSubmitThread`, `useThreads`) manage the primary data flow. TanStack Query handles server state; localStorage stores user settings. Styles use Tailwind CSS v4 with CSS variables.

### IM Channels

Channel workers (Feishu, Slack, Telegram, DingTalk, WeCom, WeChat) run inside the Gateway process, communicate with the agent through the LangGraph SDK HTTP client, and use a message bus (`message_bus.py`) for async pub/sub dispatching. Feishu and DingTalk support streaming in-place card updates. All channels use outbound connections (WebSocket or long-polling) — no public IP required.

### Database

Default: SQLite with WAL journal mode (`database.backend: sqlite`, at `.deer-flow/data/deerflow.db`). Postgres supported for production via `database.backend: postgres` with `$DATABASE_URL`. A single backend drives both the LangGraph checkpointer and DeerFlow application data (runs, feedback, events, thread metadata). Legacy standalone `checkpointer` config still works but is deprecated.

### Testing & CI

- Backend: `pytest` with `ruff` for linting/formatting (line length 240, double quotes). Regression tests cover sandbox mode detection, provisioner kubeconfig, memory updater, harness boundary enforcement, and embedded client conformance against Gateway response schemas.
- Frontend: `vitest` for unit tests (mirroring `src/` layout under `tests/unit/`), `playwright` for E2E tests (mocked backend APIs, real browser interactions). ESLint + Prettier enforced.
- CI workflows: `backend-unit-tests.yml`, `frontend-unit-tests.yml`, `e2e-tests.yml` (on frontend changes only), `lint-check.yml`, `container.yaml`.
- TDD is mandatory for backend — every feature or bug fix must include unit tests.
