# Fieldnote: Campaign Ops Worker

A local AI worker prototype for investigating marketing data incidents. It can inspect campaign metrics and conversion deliveries, retrieve a local runbook, propose a bounded remediation, wait for human approval, apply the change to the local database, and verify the result from the underlying events.

All records and incidents are synthetic. The app does not connect to company systems or third-party business applications.

## Demo scenarios

- **Repeated deliveries:** Autumn Launch has three repeated webhook deliveries. The worker should identify the duplicate idempotency keys, preview quarantine of the later deliveries, wait for approval, then verify that the count falls from 23 to 20.
- **Missing attribution:** Creator Referral has three accepted conversion events whose campaign ID is missing but whose source code maps uniquely to that campaign. The worker should preview the repair, wait for approval, then verify the attributed count rises from 10 to 13.
- **Ambiguous request:** The worker should ask for clarification when the campaign or source cannot be determined safely.
- **Transient read failure:** Use the “Simulate transient failure” control before starting a run. The next campaign-list read returns a single simulated error; the worker should observe it and make a bounded retry.

## Run locally

1. Install [Ollama](https://ollama.com/download) on Windows and pull a local model in PowerShell:

   ```powershell
   ollama pull qwen3:8b
   ```

   This model download is several GB. If it is too large for your machine, try `qwen3:4b` and set `LLM_MODEL=qwen3:4b` in `.env`.
2. Copy `.env.example` to `.env`:

   ```powershell
   Copy-Item .env.example .env
   ```

   Defaults point the backend container at Ollama on the Windows host. `LLM_API_KEY=ollama` is a compatibility placeholder for local Ollama, not a secret.
3. Start Docker Desktop.
4. From the repository root run:

   ```powershell
   docker compose up --build
   ```

5. Open [http://localhost:5173](http://localhost:5173). The API is at [http://localhost:8000](http://localhost:8000), with interactive API docs at [http://localhost:8000/docs](http://localhost:8000/docs).

The first run creates the PostgreSQL schema and seeds the synthetic records. Use **Reset demo** to return the incidents to their original state. The backend calls Ollama's OpenAI-compatible Chat Completions endpoint. You can point `LLM_BASE_URL` to another compatible endpoint and set its key in `LLM_API_KEY`. Do not commit `.env`.

## Architecture

```text
React interface
      │ HTTP
      ▼
FastAPI worker API ── Ollama (OpenAI-compatible Chat Completions API)
      │                       │ function tool calls
      ├── task/run state      ▼
      ├── action timeline   bounded local tool registry
      ├── approval gate      ├── campaign metrics
      └── audit history      ├── event inspection
              │             ├── local runbook search
              └─────────────┴── preview/apply/verify
                            ▼
                       PostgreSQL
```

Each run stores the task, tool-call conversation, observations, and action history. Tools are a fixed allowlist; the model cannot execute arbitrary SQL or reach external business systems. A remediation is first represented as a preview in PostgreSQL. Approval is a separate endpoint. The approved operation is transactional and rechecks its target rows before changing them. The agent must perform a verification read before it can report success.

## Design decisions

- **Synthetic, deterministic records:** the provided public dataset suggestions did not include the conversion delivery lineage needed to prove a duplicate repair. Purpose-built fixtures make the expected before/after result reproducible and avoid introducing unrelated rows or licensing requirements.
- **Rules for data facts, model for task control:** SQL determines counts, eligible event IDs, and whether a source-code mapping is unique. The model interprets the request, chooses tools, and explains results; it does not invent numeric evidence. A verification event compares the post-change metric to the exact expected count computed from the prepared preview.
- **Human approval for writes:** the worker can investigate autonomously, but a user must approve every proposed data change. A rejection leaves the source records unchanged.
- **Per-run memory and audit:** conversation and tool observations persist in PostgreSQL, while run events provide a reviewable timeline. This is task memory, not cross-user profiling or long-term personal memory.
- **Local mock application:** the tools perform real reads and updates against this prototype's database. Docker keeps the demo self-contained. Kafka and cloud deployment are excluded from the first version to reduce setup burden; a webhook/Kafka consumer could later replace the deterministic seed loader.
- **Runbook retrieval:** the small local runbook set is searched lexically and returned with its source ID. A vector database would add little value for three short documents.

## Agent evaluation checklist

For each scenario, record whether the worker selected relevant tools, based its diagnosis on returned evidence, handled tool errors or ambiguity safely, respected the approval boundary, changed only eligible rows, and verified the final metric. The demo should show both a successful approved repair and a safe stop/clarification case. Do not judge success from the model's final prose alone; inspect the metric query and audit trail.

## Assumptions and limitations

- The demo date is fixed to **October 4, 2026** so that the prompts and expected counts are repeatable.
- The mock campaign data is not a benchmark dataset and does not represent a real advertiser.
- Runbook lookup is lexical, not semantic retrieval. The fixture is intentionally small.
- The worker uses Ollama's OpenAI-compatible function calling by default. Ollama must be installed, running on the host, and have the selected model pulled locally.
- Tool execution is synchronous and has a ten-round limit. There is no background job queue, multi-agent execution, or long-term cross-run learning.
- The browser provides a dashboard and approval interface; the agent uses the application's restricted APIs rather than general browser automation.
- The transient failure is a controlled demo injection, not a comprehensive production reliability system.
- A production deployment would need authentication, authorization, secret management, stronger concurrency controls, observability, rate limits, and a review of data retention.

## What to build next

Add a real event ingestion adapter with idempotent writes, evaluate the worker over a broader prompt and failure set, add authentication and role-based approvals, and deploy a secured sandbox. Browser interaction can be added as a separate tool if a target workflow requires it.

## Models and components

- **Model:** Ollama `qwen3:8b` by default via its OpenAI-compatible Chat Completions API; `LLM_BASE_URL`, `LLM_MODEL`, and `LLM_API_KEY` are configurable.
- **Backend:** Python, FastAPI, Psycopg 3.
- **Frontend:** React, Vite, Lucide icons.
- **Storage:** PostgreSQL 16.
- **Runtime:** Docker Compose.
- **External service:** none for the default local setup. If you change the endpoint to a hosted service, task text and tool observations are sent to that provider; campaign data and simulated company actions stay local.
