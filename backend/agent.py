import json
import os
import urllib.error
import urllib.request
from uuid import uuid4

from db import connection
from tools import (ToolFailure, get_campaign_metrics, inspect_events, list_campaigns,
                   prepare_remediation, search_runbooks)


MODEL = os.getenv("LLM_MODEL", "qwen3:8b")
BASE_URL = os.getenv("LLM_BASE_URL", "http://host.docker.internal:11434/v1").rstrip("/")
API_KEY = os.getenv("LLM_API_KEY", "ollama")
MAX_TOOL_ROUNDS = 10

SYSTEM_PROMPT = """You are an operations worker for a simulated campaign analytics system.
Your job is to investigate the user's goal and use the available tools. Do not claim that
anything changed unless an approved action was applied and a verification read succeeded.
Start by discovering the relevant campaign and date if the request is not explicit. Inspect
both aggregate metrics and event-level evidence. Search the runbook and follow its safety
rules. For a safe repair, call prepare_remediation only after collecting evidence; it returns
a preview and pauses for human approval. Do not invent IDs or data. If there are multiple
plausible campaigns, dates, or source mappings, call ask_clarification and stop. Tool errors
are observations: retry a transient read once when appropriate, then explain the failure.
After approval, verify metrics and inspect the affected event state. Keep the final report
concise and include concrete evidence. You may only access this simulated application through
the listed tools; never write arbitrary SQL or call external services."""

TOOLS = [
    {"type": "function", "function": {"name": "list_campaigns", "description": "List campaigns and their source codes.", "parameters": {"type": "object", "properties": {}, "additionalProperties": False}}},
    {"type": "function", "function": {"name": "get_campaign_metrics", "description": "Read conversion metrics and unattributed counts for one campaign and day.", "parameters": {"type": "object", "properties": {"campaign_id": {"type": "string"}, "event_date": {"type": "string", "description": "YYYY-MM-DD"}}, "required": ["campaign_id", "event_date"], "additionalProperties": False}}},
    {"type": "function", "function": {"name": "inspect_events", "description": "Inspect conversion delivery rows; issue can be all, duplicates, or unattributed.", "parameters": {"type": "object", "properties": {"campaign_id": {"type": "string"}, "event_date": {"type": "string"}, "issue": {"type": "string", "enum": ["all", "duplicates", "unattributed"]}}, "required": ["campaign_id", "event_date"], "additionalProperties": False}}},
    {"type": "function", "function": {"name": "search_runbooks", "description": "Search the local operator runbooks for safe diagnosis and remediation steps.", "parameters": {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"], "additionalProperties": False}}},
    {"type": "function", "function": {"name": "prepare_remediation", "description": "Create a safe preview; any resulting change waits for explicit user approval.", "parameters": {"type": "object", "properties": {"action_type": {"type": "string", "enum": ["quarantine_duplicate_deliveries", "repair_unattributed_events"]}, "campaign_id": {"type": "string"}, "event_date": {"type": "string"}}, "required": ["action_type", "campaign_id", "event_date"], "additionalProperties": False}}},
    {"type": "function", "function": {"name": "ask_clarification", "description": "Pause and ask the user a specific question when the task cannot safely proceed.", "parameters": {"type": "object", "properties": {"question": {"type": "string"}}, "required": ["question"], "additionalProperties": False}}},
]


def call_model(messages):
    if not API_KEY:
        raise RuntimeError("LLM_API_KEY is blank. Set it to 'ollama' for local Ollama or provide the key for your configured endpoint.")
    body = json.dumps({"model": MODEL, "messages": messages, "tools": TOOLS, "tool_choice": "auto", "temperature": 0.1, "stream": False}).encode("utf-8")
    req = urllib.request.Request(f"{BASE_URL}/chat/completions", data=body, headers={"Authorization": f"Bearer {API_KEY}", "Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=60) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:500]
        raise RuntimeError(f"Configured model endpoint returned HTTP {exc.code}: {detail}") from exc
    except (urllib.error.URLError, TimeoutError) as exc:
        raise RuntimeError(f"Could not reach the configured model API: {exc}") from exc
    return payload["choices"][0]["message"]


def record_event(run_id, kind, details, tool_name=None):
    with connection() as conn:
        conn.execute("INSERT INTO run_events(run_id,kind,tool_name,details) VALUES (%s,%s,%s,%s::jsonb)",
                     (run_id, kind, tool_name, json.dumps(details, default=str)))


def load_run(run_id):
    with connection() as conn:
        run = conn.execute("SELECT * FROM runs WHERE run_id=%s", (run_id,)).fetchone()
    return dict(run) if run else None


def save_run(run_id, *, status=None, messages=None, summary=None, pending_tool_call_id=None, set_pending=False):
    fields = ["updated_at=now()"]
    values = []
    if status is not None:
        fields.append("status=%s"); values.append(status)
    if messages is not None:
        fields.append("messages=%s::jsonb"); values.append(json.dumps(messages))
    if summary is not None:
        fields.append("summary=%s"); values.append(summary)
    if set_pending:
        fields.append("pending_tool_call_id=%s"); values.append(pending_tool_call_id)
    values.append(run_id)
    with connection() as conn:
        conn.execute(f"UPDATE runs SET {', '.join(fields)} WHERE run_id=%s", values)


def tool_result(run_id, name, arguments):
    try:
        if name == "list_campaigns":
            result = list_campaigns()
        elif name == "get_campaign_metrics":
            result = get_campaign_metrics(**arguments)
        elif name == "inspect_events":
            result = inspect_events(**arguments)
        elif name == "search_runbooks":
            result = search_runbooks(**arguments)
        elif name == "prepare_remediation":
            result = prepare_remediation(run_id=run_id, **arguments)
        elif name == "ask_clarification":
            result = {"needs_input": True, "question": arguments["question"]}
        else:
            result = {"error": "Tool is not available"}
    except (ToolFailure, ValueError) as exc:
        result = {"error": str(exc), "retryable": isinstance(exc, ToolFailure)}
    if name == "list_campaigns" and result.get("error"):
        with connection() as conn:
            prior_failures = conn.execute(
                """SELECT count(*) AS n FROM run_events
                   WHERE run_id=%s AND kind='tool' AND tool_name='list_campaigns'
                     AND details->'result'->>'retryable'='true'""",
                (run_id,),
            ).fetchone()["n"]
        if prior_failures >= 1:
            result = {"error": "The campaign-list read failed again. Retry limit reached; stop and report the issue.", "retryable": False}
    if name == "get_campaign_metrics" and "error" not in result:
        with connection() as conn:
            action = conn.execute(
                """SELECT action_type,preview FROM remediation_actions
                   WHERE run_id=%s AND campaign_id=%s AND event_date=%s AND status='applied'
                   ORDER BY executed_at DESC LIMIT 1""",
                (run_id, arguments["campaign_id"], arguments["event_date"]),
            ).fetchone()
        if action:
            preview = dict(action["preview"])
            expected = int(preview["expected_attributed_conversions"])
            checks = [result["attributed_conversions"] == expected]
            evidence = {"action_id": str(action["action_id"]),
                        "expected_attributed_conversions": expected,
                        "actual_attributed_conversions": result["attributed_conversions"]}
            if action["action_type"] == "repair_unattributed_events":
                checks.append(result["unattributed_conversions"] == 0)
                evidence["remaining_unattributed_conversions"] = result["unattributed_conversions"]
            else:
                duplicates = inspect_events(arguments["campaign_id"], arguments["event_date"], "duplicates")
                checks.append(duplicates["count"] == 0)
                evidence["remaining_duplicate_deliveries"] = duplicates["count"]
            result["verification"] = {"ok": all(checks), **evidence}
            record_event(run_id, "verification", result["verification"])
    record_event(run_id, "tool", {"arguments": arguments, "result": result}, name)
    return result


def run_loop(run_id):
    run = load_run(run_id)
    if not run:
        return None
    messages = list(run["messages"])
    try:
        for _ in range(MAX_TOOL_ROUNDS):
            reply = call_model(messages)
            assistant_message = {k: reply[k] for k in ("role", "content", "tool_calls") if k in reply}
            messages.append(assistant_message)
            calls = reply.get("tool_calls") or []
            if not calls:
                text = reply.get("content") or "The agent ended without a response."
                with connection() as conn:
                    verified = conn.execute(
                        """SELECT 1 FROM remediation_actions a
                           WHERE a.run_id=%s AND a.status='applied'
                             AND EXISTS (SELECT 1 FROM run_events e WHERE e.run_id=a.run_id AND e.kind='verification'
                                         AND e.details->>'action_id'=a.action_id::text AND e.details->>'ok'='true')
                           ORDER BY a.executed_at DESC LIMIT 1""",
                        (run_id,),
                    ).fetchone()
                if verified:
                    save_run(run_id, status="completed", messages=messages, summary=text, set_pending=True, pending_tool_call_id=None)
                    record_event(run_id, "completed", {"summary": text})
                else:
                    save_run(run_id, status="failed", messages=messages, summary="The agent stopped before it verified the outcome. " + text, set_pending=True, pending_tool_call_id=None)
                    record_event(run_id, "failed", {"reason": "Agent stopped before verification", "summary": text})
                break
            save_run(run_id, messages=messages)
            paused = False
            for call_index, call in enumerate(calls):
                name = call["function"]["name"]
                raw_arguments = call["function"].get("arguments") or "{}"
                arguments = json.loads(raw_arguments) if isinstance(raw_arguments, str) else raw_arguments
                result = tool_result(run_id, name, arguments)
                messages.append({"role": "tool", "tool_call_id": call["id"], "content": json.dumps(result, default=str)})
                if result.get("awaiting_approval"):
                    save_run(run_id, status="awaiting_approval", messages=messages, set_pending=True, pending_tool_call_id=call["id"])
                    record_event(run_id, "approval_requested", result)
                    paused = True
                    for remaining in calls[call_index + 1:]:
                        canceled = {"error": "Not executed because the run paused for human approval."}
                        messages.append({"role": "tool", "tool_call_id": remaining["id"], "content": json.dumps(canceled)})
                        record_event(run_id, "tool", {"arguments": {}, "result": canceled}, remaining["function"]["name"])
                    save_run(run_id, messages=messages)
                    break
                if result.get("needs_input"):
                    save_run(run_id, status="awaiting_input", messages=messages, summary=result["question"], set_pending=True, pending_tool_call_id=None)
                    record_event(run_id, "clarification_requested", result)
                    paused = True
                    for remaining in calls[call_index + 1:]:
                        canceled = {"error": "Not executed because the run paused for user clarification."}
                        messages.append({"role": "tool", "tool_call_id": remaining["id"], "content": json.dumps(canceled)})
                        record_event(run_id, "tool", {"arguments": {}, "result": canceled}, remaining["function"]["name"])
                    save_run(run_id, messages=messages)
                    break
            if paused:
                break
            save_run(run_id, messages=messages)
        else:
            save_run(run_id, status="failed", messages=messages, summary="The agent reached its action limit before completing verification.", set_pending=True, pending_tool_call_id=None)
            record_event(run_id, "failed", {"reason": "Action limit reached"})
    except Exception as exc:
        save_run(run_id, status="failed", messages=messages, summary=str(exc), set_pending=True, pending_tool_call_id=None)
        record_event(run_id, "failed", {"reason": str(exc)})
    return get_run_details(run_id)


def start_run(task):
    run_id = str(uuid4())
    messages = [{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": task}]
    with connection() as conn:
        conn.execute("INSERT INTO runs(run_id,task,status,messages) VALUES (%s,%s,'running',%s::jsonb)", (run_id, task, json.dumps(messages)))
    record_event(run_id, "started", {"task": task})
    return run_loop(run_id)


def continue_run(run_id, answer):
    run = load_run(run_id)
    if not run or run["status"] != "awaiting_input":
        raise ValueError("This run is not waiting for clarification")
    messages = list(run["messages"])
    messages.append({"role": "user", "content": answer})
    save_run(run_id, status="running", messages=messages, summary="", set_pending=True, pending_tool_call_id=None)
    record_event(run_id, "user_input", {"answer": answer})
    return run_loop(run_id)


def approve_run(run_id):
    run = load_run(run_id)
    if not run or run["status"] != "awaiting_approval":
        raise ValueError("This run is not waiting for approval")
    messages = list(run["messages"])
    pending_id = run["pending_tool_call_id"]
    with connection() as conn:
        action = conn.execute("SELECT action_id FROM remediation_actions WHERE run_id=%s AND status='pending' ORDER BY created_at DESC LIMIT 1", (run_id,)).fetchone()
    if not action or not pending_id:
        raise ValueError("Pending action state is incomplete")
    from tools import apply_approved_action
    result = apply_approved_action(str(action["action_id"]))
    record_event(run_id, "approved_and_applied", result)
    replacement = json.dumps({"approved": True, "result": result})
    for message in reversed(messages):
        if message.get("role") == "tool" and message.get("tool_call_id") == pending_id:
            message["content"] = replacement
            break
    else:
        messages.append({"role": "user", "content": f"Operator approved action {action['action_id']}. It was applied: {replacement}. Continue by verifying the result."})
    save_run(run_id, status="running", messages=messages, summary="", set_pending=True, pending_tool_call_id=None)
    return run_loop(run_id)


def reject_run(run_id):
    run = load_run(run_id)
    if not run or run["status"] != "awaiting_approval":
        raise ValueError("This run is not waiting for approval")
    with connection() as conn:
        action = conn.execute("SELECT action_id FROM remediation_actions WHERE run_id=%s AND status='pending' ORDER BY created_at DESC LIMIT 1", (run_id,)).fetchone()
    if action:
        from tools import reject_action
        result = reject_action(str(action["action_id"]))
        record_event(run_id, "approval_rejected", result)
    save_run(run_id, status="failed", summary="The proposed change was rejected. No data was changed.", set_pending=True, pending_tool_call_id=None)
    record_event(run_id, "stopped", {"reason": "User rejected remediation"})
    return get_run_details(run_id)


def get_run_details(run_id):
    with connection() as conn:
        run = conn.execute("SELECT run_id,task,status,summary,created_at,updated_at FROM runs WHERE run_id=%s", (run_id,)).fetchone()
        if not run:
            return None
        events = conn.execute("SELECT event_id,kind,tool_name,details,created_at FROM run_events WHERE run_id=%s ORDER BY event_id", (run_id,)).fetchall()
        pending = conn.execute("SELECT action_id,preview,status FROM remediation_actions WHERE run_id=%s AND status='pending' ORDER BY created_at DESC LIMIT 1", (run_id,)).fetchone()
    result = dict(run)
    result["run_id"] = str(result["run_id"])
    result["created_at"] = result["created_at"].isoformat()
    result["updated_at"] = result["updated_at"].isoformat()
    result["events"] = []
    for event in events:
        item = dict(event)
        item["created_at"] = item["created_at"].isoformat()
        result["events"].append(item)
    result["pending_action"] = dict(pending) if pending else None
    return result
