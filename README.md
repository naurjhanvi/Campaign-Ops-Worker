# Fieldnote: Campaign Ops Worker

A local, rule-based worker prototype for investigating marketing data incidents. It inspects campaign metrics and conversion deliveries, retrieves a local runbook, proposes a bounded remediation, waits for human approval, applies the change to the local database, and verifies the result from the underlying events. It needs no model download or API key and makes no external service calls after Docker images are built.

All records and incidents are synthetic. The app does not connect to company systems or third-party business applications.

## Demo scenarios

- **Repeated deliveries:** Autumn Launch has three repeated webhook deliveries. The worker should identify the duplicate idempotency keys, preview quarantine of the later deliveries, wait for approval, then verify that the count falls from 23 to 20.
- **Missing attribution:** Creator Referral has three accepted conversion events whose campaign ID is missing but whose source code maps uniquely to that campaign. The worker should preview the repair, wait for approval, then verify the attributed count rises from 10 to 13.
- **Ambiguous request:** The worker should ask for clarification when the campaign or source cannot be determined safely.
- **Transient read failure:** Use the “Simulate transient failure” control before starting a run. The next campaign-list read returns a single simulated error; the worker should observe it and make a bounded retry.

## Run locally

1. Start Docker Desktop.
2. From the repository root run:

   ```powershell
   docker compose up --build
   ```

3. Open [http://localhost:5173](http://localhost:5173). The API is at [http://localhost:8000](http://localhost:8000), with interactive API docs at [http://localhost:8000/docs](http://localhost:8000/docs).

The first run creates the PostgreSQL schema and seeds the synthetic records. Use **Reset demo** to return the incidents to their original state.

## Architecture

```text
React interface
      │ HTTP
      ▼
FastAPI worker API ── bounded local tool registry
      ├── deterministic task routing
      ├── campaign metrics and event inspection
      ├── local runbook search
      ├── approval gate and audit history
      └── preview/apply/verify ── PostgreSQL
```

Each run stores the task, tool-call conversation, observations, and action history. Tools are fixed and local; the worker cannot execute arbitrary SQL or reach external business systems. A remediation is first represented as a preview in PostgreSQL. Approval is a separate endpoint. The approved operation is transactional and rechecks its target rows before changing them. The worker must perform a verification read before it can report success.

## Design decisions

- **Synthetic, deterministic records:** the provided public dataset suggestions did not include the conversion delivery lineage needed to prove a duplicate repair. Purpose-built fixtures make the expected before/after result reproducible and avoid introducing unrelated rows or licensing requirements.
- **Rules for task routing and data facts:** built-in phrase matching selects one of the supported demo workflows. SQL determines counts, eligible event IDs, and whether a source-code mapping is unique. A verification event compares the post-change metric to the exact expected count computed from the prepared preview.
- **Human approval for writes:** the worker can investigate autonomously, but a user must approve every proposed data change. A rejection leaves the source records unchanged.
- **Per-run memory and audit:** conversation and tool observations persist in PostgreSQL, while run events provide a reviewable timeline. This is task memory, not cross-user profiling or long-term personal memory.
- **Local mock application:** the tools perform real reads and updates against this prototype's database. Docker keeps the demo self-contained. Kafka and cloud deployment are excluded from the first version to reduce setup burden; a webhook/Kafka consumer could later replace the deterministic seed loader.
- **Runbook retrieval:** the small local runbook set is searched lexically and returned with its source ID. A vector database would add little value for three short documents.

## Agent evaluation checklist

For each scenario, record whether the worker selected the intended workflow, based its diagnosis on returned evidence, handled tool errors or ambiguity safely, respected the approval boundary, changed only eligible rows, and verified the final metric. The demo should show both a successful approved repair and a safe stop/clarification case. Inspect the metric query and audit trail.

## Assumptions and limitations

- The demo date is fixed to **October 4, 2026** so that the prompts and expected counts are repeatable.
- The mock campaign data is not a benchmark dataset and does not represent a real advertiser.
- Runbook lookup is lexical, not semantic retrieval. The fixture is intentionally small.
- Task routing is deterministic phrase matching, not a general-purpose language model. It supports the named synthetic campaigns and duplicate-delivery or missing-attribution incidents.
- Tool execution is synchronous and has a ten-round limit. There is no background job queue, multi-agent execution, or long-term cross-run learning.
- The browser provides a dashboard and approval interface; the agent uses the application's restricted APIs rather than general browser automation.
- The transient failure is a controlled demo injection, not a comprehensive production reliability system.
- A production deployment would need authentication, authorization, secret management, stronger concurrency controls, observability, rate limits, and a review of data retention.

## What to build next

Add a real event ingestion adapter with idempotent writes, evaluate the worker over a broader prompt and failure set, add authentication and role-based approvals, and deploy a secured sandbox. Browser interaction can be added as a separate tool if a target workflow requires it.

## Components

- **Worker:** deterministic local policy; no model or API credentials.
- **Backend:** Python, FastAPI, Psycopg 3.
- **Frontend:** React, Vite, Lucide icons.
- **Storage:** PostgreSQL 16.
- **Runtime:** Docker Compose.
- **External service:** none.
