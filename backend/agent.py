import json
import re
from uuid import uuid4

from db import connection
from tools import (ToolFailure, get_campaign_metrics, inspect_events, list_campaigns,
                   prepare_remediation, search_runbooks)


MAX_TOOL_ROUNDS = 10
DEMO_DATE = "2026-10-04"

SYSTEM_PROMPT = """Local campaign operations workflow. This prompt is retained in the per-run
audit conversation; execution uses the deterministic policy in this service and never calls
an external model. Supported repairs require matching campaign and issue intent, evidence from
the local database and runbook, explicit operator approval, and a successful verification read."""

def _users(messages):
    return [message.get("content", "") for message in messages if message.get("role") == "user"]


def _task_context(messages):
    user_messages = _users(messages)
    text = " ".join(user_messages).lower()
    date_match = re.search(r"\b\d{4}-\d{2}-\d{2}\b", " ".join(user_messages))
    event_date = date_match.group(0) if date_match else DEMO_DATE

    campaigns = {
        "CMP-101": ("autumn launch", "autumn_launch"),
        "CMP-202": ("creator referral", "creator_referral"),
        "CMP-303": ("northstar retargeting", "northstar_retargeting"),
    }
    campaign_id = next(
        (campaign_id for campaign_id, names in campaigns.items()
         if campaign_id.lower() in text or any(name in text for name in names)),
        None,
    )
    duplicate_terms = ("duplicate", "repeated delivery", "conversion spike", "spike", "overcount")
    attribution_terms = ("attribution", "unattributed", "missing campaign", "mapping gap", "conversion drop")
    wants_duplicates = any(term in text for term in duplicate_terms)
    wants_attribution = any(term in text for term in attribution_terms)
    issue = "duplicates" if wants_duplicates and not wants_attribution else "unattributed" if wants_attribution and not wants_duplicates else None
    return {"campaign_id": campaign_id, "event_date": event_date, "issue": issue}


def _called_tools(messages):
    calls = []
    results = {}
    for message in messages:
        if message.get("role") == "assistant":
            calls.extend(message.get("tool_calls") or [])
        elif message.get("role") == "tool":
            try:
                results[message.get("tool_call_id")] = json.loads(message.get("content") or "{}")
            except (TypeError, ValueError):
                results[message.get("tool_call_id")] = {}
    return calls, results


def _tool_step(name, arguments):
    call = {"id": str(uuid4()), "type": "function", "function": {"name": name, "arguments": json.dumps(arguments)}}
    return {"role": "assistant", "content": None, "tool_calls": [call]}


def _clarification(context):
    if context["campaign_id"] is None and context["issue"] is None:
        question = "Which campaign should I investigate, and is the issue repeated deliveries or missing attribution?"
    elif context["campaign_id"] is None:
        question = "Which campaign should I investigate: Autumn Launch, Creator Referral, or Northstar Retargeting?"
    else:
        question = "Should I investigate repeated deliveries or missing campaign attribution?"
    return _tool_step("ask_clarification", {"question": question})


def next_worker_step(messages):
    """Choose the next bounded local workflow step without calling a model or network."""
    context = _task_context(messages)
    calls, results = _called_tools(messages)
    if calls and calls[-1]["function"]["name"] == "ask_clarification":
        answer_result = results.get(calls[-1]["id"], {})
        if answer_result.get("needs_input") and len(_users(messages)) < 2:
            return _clarification(context)

    if context["campaign_id"] is None or context["issue"] is None:
        return _clarification(context)

    completed = [(call["function"]["name"], results.get(call["id"], {})) for call in calls]
    last_name, last_result = completed[-1] if completed else (None, {})
    campaign_id = context["campaign_id"]
    event_date = context["event_date"]
    issue = context["issue"]

    if last_name is None or last_name == "ask_clarification":
        return _tool_step("list_campaigns", {})
    if last_name == "list_campaigns":
        if isinstance(last_result, dict):
            if last_result.get("retryable"):
                return _tool_step("list_campaigns", {})
            if last_result.get("error"):
                return {"role": "assistant", "content": f"I stopped because campaign data could not be read: {last_result['error']}"}
        matches = [campaign for campaign in last_result if campaign["campaign_id"] == campaign_id]
        if len(matches) != 1:
            return _clarification(context)
        return _tool_step("get_campaign_metrics", {"campaign_id": campaign_id, "event_date": event_date})
    if last_name == "get_campaign_metrics" and "verification" not in last_result:
        if last_result.get("error"):
            return {"role": "assistant", "content": f"I could not read campaign metrics: {last_result['error']}"}
        return _tool_step("inspect_events", {"campaign_id": campaign_id, "event_date": event_date, "issue": issue})
    if last_name == "inspect_events":
        if last_result.get("count", 0) == 0:
            metrics = next((result for name, result in reversed(completed) if name == "get_campaign_metrics"), {})
            if issue == "duplicates":
                attributed = metrics.get("attributed_conversions")
                unique_orders = metrics.get("unique_order_keys")
                clean = attributed is not None and unique_orders is not None and attributed == unique_orders
                detail = f"{attributed} attributed conversions match {unique_orders} unique orders."
            else:
                clean = metrics.get("unattributed_conversions") == 0
                detail = "There are no remaining unattributed conversions."
            if clean:
                verification = {"ok": True, "no_change_needed": True, "campaign_id": campaign_id,
                                "event_date": event_date, "issue": issue, "evidence": detail}
                return {"role": "assistant", "content": f"No change was needed. {detail}",
                        "no_change_verification": verification}
            return {"role": "assistant", "content": "No eligible events matched, but the campaign metrics do not confirm that the issue is clear. I stopped without preparing a change."}
        query = "duplicate conversion deliveries" if issue == "duplicates" else "missing campaign attribution"
        return _tool_step("search_runbooks", {"query": query})
    if last_name == "search_runbooks":
        if not last_result:
            return {"role": "assistant", "content": "I could not find a matching safety runbook, so I stopped without preparing a change."}
        action_type = "quarantine_duplicate_deliveries" if issue == "duplicates" else "repair_unattributed_events"
        return _tool_step("prepare_remediation", {"action_type": action_type, "campaign_id": campaign_id, "event_date": event_date})
    if last_name == "prepare_remediation":
        if last_result.get("approved"):
            return _tool_step("get_campaign_metrics", {"campaign_id": campaign_id, "event_date": event_date})
        if last_result.get("awaiting_approval"):
            return {"role": "assistant", "content": "The proposed change is ready for your review."}
        return {"role": "assistant", "content": f"I could not prepare a safe change: {last_result.get('error', 'The preview could not be prepared.')}"}
    if last_name == "get_campaign_metrics" and last_result.get("verification"):
        verification = last_result["verification"]
        if verification.get("ok"):
            return {"role": "assistant", "content": f"The approved repair is complete and verified. The campaign now has {verification['actual_attributed_conversions']} attributed conversions."}
        return {"role": "assistant", "content": "The approved repair was applied, but the verification check did not pass. Review the event and metric evidence before taking further action."}
    if last_result.get("error"):
        return {"role": "assistant", "content": f"I stopped after a tool error: {last_result['error']}"}
    return {"role": "assistant", "content": "The local workflow reached a state it cannot safely continue from."}


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
    if name == "list_campaigns" and isinstance(result, dict) and result.get("error"):
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
                """SELECT action_id,action_type,preview FROM remediation_actions
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
            reply = next_worker_step(messages)
            assistant_message = {k: reply[k] for k in ("role", "content", "tool_calls") if k in reply}
            messages.append(assistant_message)
            calls = reply.get("tool_calls") or []
            if not calls:
                text = reply.get("content") or "The agent ended without a response."
                no_change_verification = reply.get("no_change_verification")
                if no_change_verification and no_change_verification.get("ok"):
                    record_event(run_id, "verification", no_change_verification)
                    save_run(run_id, status="completed", messages=messages, summary=text, set_pending=True, pending_tool_call_id=None)
                    record_event(run_id, "completed", {"summary": text, "no_change_needed": True})
                    break
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
                if isinstance(result, dict) and result.get("awaiting_approval"):
                    save_run(run_id, status="awaiting_approval", messages=messages, set_pending=True, pending_tool_call_id=call["id"])
                    record_event(run_id, "approval_requested", result)
                    paused = True
                    for remaining in calls[call_index + 1:]:
                        canceled = {"error": "Not executed because the run paused for human approval."}
                        messages.append({"role": "tool", "tool_call_id": remaining["id"], "content": json.dumps(canceled)})
                        record_event(run_id, "tool", {"arguments": {}, "result": canceled}, remaining["function"]["name"])
                    save_run(run_id, messages=messages)
                    break
                if isinstance(result, dict) and result.get("needs_input"):
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
