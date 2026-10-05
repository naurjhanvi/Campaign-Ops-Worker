> **Development update:** After submitting the Google Form, I continued building this project beyond the original time constraint. The current version now uses a local Ollama model to interpret requests and select tools. I did not mention Ollama in the form because this integration was added during that continued development. The approval gate and verification checks are enforced by the application.

# Fieldnote: Campaign Ops Worker

A local campaign operations worker prototype for investigating marketing data incidents. An Ollama model interprets the request and selects from a fixed set of local tools. The worker reads campaign metrics and event deliveries, retrieves a local runbook, proposes a bounded remediation, waits for human approval, applies the change to the local database, and verifies the result.

All records and incidents are synthetic. The app does not connect to company systems or third-party business applications. By default, the language model runs locally through Ollama; prompts and tool observations are sent only to the Ollama server configured for this app.

## Demo scenarios

- **Repeated deliveries:** Autumn Launch has three repeated webhook deliveries. The worker should identify the duplicate idempotency keys, preview quarantine of the later deliveries, wait for approval, then verify that the count falls from 23 to 20.
- **Missing attribution:** Creator Referral has three accepted conversion events whose campaign ID is missing but whose source code maps uniquely to that campaign. The worker should preview the repair, wait for approval, then verify the attributed count rises from 10 to 13.
- **Ambiguous request:** The worker should ask for clarification when the campaign or issue cannot be determined safely.
- **Transient read failure:** Use the “Simulate transient failure” control before starting a run. The next campaign-list read returns a single simulated error; the worker should observe it and make a bounded retry.
- **Already resolved or empty date:** The worker confirms that no change is needed only when metrics show records exist and those records do not exhibit the requested issue. It stops without a change when evidence is empty or inconsistent.

## Run locally with Ollama

1. Install and start [Ollama](https://ollama.com/download).
2. In PowerShell, download the default model once:

   ```powershell
   ollama pull qwen3:8b
   ```

   Keep Ollama running in the background. The download needs a stable internet connection; after it completes, inference is local.
3. From the repository root, create `.env` if you do not already have one:

   ```powershell
   if (-not (Test-Path .env)) { Copy-Item .env.example .env }
   ```

   The defaults point the Docker API container to Ollama on the same computer at `host.docker.internal:11434`. If needed, edit `LLM_MODEL` in `.env` to match a model you pulled.
4. Start or rebuild the app:

   ```powershell
   docker compose up --build
   ```

5. Open [http://localhost:5173](http://localhost:5173). The API is at [http://localhost:8000](http://localhost:8000), with interactive API docs at [http://localhost:8000/docs](http://localhost:8000/docs).

The first run creates the PostgreSQL schema and seeds the synthetic records. Use **Reset demo** to return the incidents to their original state. If Ollama cannot be reached, start it and check the address in `.env`. If the model is missing, run `ollama pull qwen3:8b`. For a network-independent demonstration, set `WORKER_MODE=rules` in `.env` and restart Compose; that mode uses built-in phrase matching instead of a language model.

## Architecture

```text
React interface
      │ HTTP
      ▼
FastAPI worker API ── Ollama local chat/tool-calling API
      │                 (or optional built-in rules mode)
      ├── bounded local tool registry
      │     ├── campaign metrics and event inspection
      │     ├── local runbook search
      │     └── preview remediation
      ├── explicit approval gate and audit history
      └── transactional apply + verification ── PostgreSQL
```

Each run stores the task, tool-call conversation, observations, and action history. The model can select only the fixed local tools; it cannot execute arbitrary SQL or reach external business systems. A remediation is first represented as a preview in PostgreSQL. Approval is a separate endpoint. The approved operation is transactional and rechecks its target rows before changing them. The worker must perform a verification read before it can report success.

## Design decisions

- **Synthetic, deterministic records:** the public dataset suggestions did not include the conversion delivery lineage needed to prove a duplicate repair. Purpose-built fixtures make the expected before/after result reproducible and avoid unrelated rows or licensing requirements.
- **Local model plus code-enforced safeguards:** Ollama helps interpret requests and choose tools. SQL determines counts, eligible event IDs, and whether a source-code mapping is unique. The API enforces the tool allowlist, human approval, transaction boundaries, and verification requirements independently of model output.
- **Human approval for writes:** the worker can investigate autonomously, but a user must approve every proposed data change. A rejection leaves the source records unchanged.
- **Per-run memory and audit:** conversation and tool observations persist in PostgreSQL, while run events provide a reviewable timeline. This is task memory, not cross-user profiling or long-term personal memory.
- **Local mock application:** tools perform real reads and updates against this prototype's database. Docker keeps the demo self-contained. Kafka and cloud deployment are excluded from the first version; a webhook/Kafka consumer could later replace the deterministic seed loader.
- **Runbook retrieval:** the small local runbook set is searched lexically and returned with its source ID. A vector database would add little value for three short documents.

## Agent evaluation checklist

For each scenario, record whether the worker selected the intended workflow, based its diagnosis on returned evidence, handled tool errors or ambiguity safely, respected the approval boundary, changed only eligible rows, and verified the final metric. The demo should show a successful approved repair and a safe stop or clarification case. Inspect the metric query and audit trail.

## Assumptions and limitations

- The demo date is fixed to **October 4, 2026** so prompts and expected counts are repeatable.
- The mock campaign data is not a benchmark dataset and does not represent a real advertiser.
- Runbook lookup is lexical, not semantic retrieval. The fixture is intentionally small.
- Ollama selects tools from this prototype's limited supported workflows. It is not a general-purpose autonomous operations agent; the rules mode provides deterministic phrase matching when Ollama is unavailable.
- The default `qwen3:8b` model must be downloaded before the first Ollama-backed run. Limited or unstable connectivity can prevent setup.
- Tool execution is synchronous and has a ten-round limit. There is no background job queue, multi-agent execution, or long-term cross-run learning.
- The browser provides a dashboard and approval interface; the agent uses the application's restricted APIs rather than general browser automation.
- The transient failure is a controlled demo injection, not a comprehensive production reliability system.
- A production deployment would need authentication, authorization, secret management, stronger concurrency controls, observability, rate limits, and a review of data retention.

## What to build next

Add a real event ingestion adapter with idempotent writes, evaluate the worker over a broader prompt and failure set, add authentication and role-based approvals, and deploy a secured sandbox. Browser interaction can be added as a separate tool if a target workflow requires it.

## Components

- **Worker:** Ollama `qwen3:8b` by default; optional built-in rules mode.
- **Backend:** Python, FastAPI, Psycopg 3, Python standard library HTTP client.
- **Frontend:** React, Vite, Lucide icons.
- **Storage:** PostgreSQL 16.
- **Runtime:** Docker Compose and local Ollama.
- **External business integrations:** none.
