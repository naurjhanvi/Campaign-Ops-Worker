from datetime import date
from uuid import uuid4

from db import connection, consume_transient_failure, json_value


class ToolFailure(Exception):
    pass


def list_campaigns():
    should_fail = False
    with connection() as conn:
        should_fail = consume_transient_failure(conn)
        rows = [] if should_fail else conn.execute("SELECT campaign_id,name,source_code,channel FROM campaigns ORDER BY campaign_id").fetchall()
    if should_fail:
        raise ToolFailure("Temporary analytics read failure (simulated HTTP 503). Retry this read once if appropriate.")
    return [dict(row) for row in rows]


def get_campaign_metrics(campaign_id: str, event_date: str):
    target_day = date.fromisoformat(event_date)
    with connection() as conn:
        campaign = conn.execute("SELECT campaign_id,name FROM campaigns WHERE campaign_id=%s", (campaign_id,)).fetchone()
        if not campaign:
            return {"error": f"Unknown campaign {campaign_id}"}
        row = conn.execute(
            """SELECT count(*) FILTER (WHERE status='accepted' AND event_type='conversion' AND campaign_id=%s) AS attributed_conversions,
                      count(DISTINCT idempotency_key) FILTER (WHERE status='accepted' AND event_type='conversion' AND (campaign_id=%s OR source_campaign_code=(SELECT source_code FROM campaigns WHERE campaign_id=%s))) AS unique_order_keys,
                      count(*) FILTER (WHERE status='accepted' AND event_type='conversion' AND campaign_id IS NULL AND source_campaign_code=(SELECT source_code FROM campaigns WHERE campaign_id=%s)) AS unattributed_conversions
               FROM event_deliveries WHERE occurred_at::date=%s""",
            (campaign_id, campaign_id, campaign_id, campaign_id, target_day),
        ).fetchone()
        return {"campaign": dict(campaign), "date": event_date, **dict(row)}


def inspect_events(campaign_id: str, event_date: str, issue: str = "all"):
    target_day = date.fromisoformat(event_date)
    with connection() as conn:
        rows = conn.execute(
            """SELECT id,delivery_id,idempotency_key,campaign_id,source_campaign_code,event_type,user_id,
                      occurred_at,received_at,status,payload
               FROM event_deliveries
               WHERE occurred_at::date=%s
                 AND (campaign_id=%s OR source_campaign_code=(SELECT source_code FROM campaigns WHERE campaign_id=%s))
               ORDER BY occurred_at,received_at""",
            (target_day, campaign_id, campaign_id),
        ).fetchall()
    records = [dict(row) for row in rows]
    for record in records:
        record["occurred_at"] = json_value(record["occurred_at"])
        record["received_at"] = json_value(record["received_at"])
        record["payload"] = dict(record["payload"])
    if issue == "duplicates":
        groups = {}
        for record in records:
            if record["status"] == "accepted" and record["campaign_id"] == campaign_id and record["event_type"] == "conversion":
                groups.setdefault(record["idempotency_key"], []).append(record)
        duplicate_count = sum(len(group) - 1 for group in groups.values() if len(group) > 1)
        records = [item for group in groups.values() if len(group) > 1 for item in group]
    elif issue == "unattributed":
        records = [r for r in records if r["campaign_id"] is None and r["event_type"] == "conversion" and r["status"] == "accepted"]
    return {"campaign_id": campaign_id, "date": event_date,
            "count": duplicate_count if issue == "duplicates" else len(records), "events": records[:100]}


def search_runbooks(query: str):
    terms = {word.lower().strip(".,?!") for word in query.split() if len(word) > 2}
    with connection() as conn:
        rows = conn.execute("SELECT runbook_id,title,body FROM runbooks").fetchall()
    ranked = []
    for row in rows:
        haystack = f"{row['title']} {row['body']}".lower()
        score = sum(1 for term in terms if term in haystack)
        if score:
            ranked.append((score, row))
    ranked.sort(key=lambda pair: pair[0], reverse=True)
    return [{"runbook_id": row["runbook_id"], "title": row["title"], "excerpt": row["body"], "matched_terms": score} for score, row in ranked[:3]]


def prepare_remediation(run_id: str, action_type: str, campaign_id: str, event_date: str):
    target_day = date.fromisoformat(event_date)
    with connection() as conn:
        campaign = conn.execute("SELECT campaign_id,name,source_code FROM campaigns WHERE campaign_id=%s", (campaign_id,)).fetchone()
        if not campaign:
            return {"error": f"Unknown campaign {campaign_id}"}
        if action_type == "quarantine_duplicate_deliveries":
            rows = conn.execute(
                """WITH repeated AS (
                     SELECT id,delivery_id,idempotency_key,received_at,
                            row_number() OVER (PARTITION BY idempotency_key ORDER BY received_at,id) AS delivery_rank
                     FROM event_deliveries
                     WHERE campaign_id=%s AND event_type='conversion' AND status='accepted' AND occurred_at::date=%s
                   ) SELECT id,delivery_id,idempotency_key FROM repeated WHERE delivery_rank>1 ORDER BY id""",
                (campaign_id, target_day),
            ).fetchall()
            ids = [row["id"] for row in rows]
            event_ids = [row["delivery_id"] for row in rows]
            action_label = "Quarantine repeated deliveries while keeping the earliest delivery for each order key."
        elif action_type == "repair_unattributed_events":
            rows = conn.execute(
                """SELECT id,delivery_id,source_campaign_code FROM event_deliveries
                   WHERE campaign_id IS NULL AND source_campaign_code=%s AND event_type='conversion'
                     AND status='accepted' AND occurred_at::date=%s ORDER BY id""",
                (campaign["source_code"], target_day),
            ).fetchall()
            ids = [row["id"] for row in rows]
            event_ids = [row["delivery_id"] for row in rows]
            action_label = "Assign missing campaign attribution using the unique matching source code."
        else:
            return {"error": "Unsupported remediation type"}
        if not ids:
            return {"error": "No eligible events matched the safe remediation rule; no action was prepared."}
        current_count = conn.execute(
            "SELECT count(*) AS n FROM event_deliveries WHERE campaign_id=%s AND event_type='conversion' AND status='accepted' AND occurred_at::date=%s",
            (campaign_id, target_day),
        ).fetchone()["n"]
        expected_count = current_count - len(ids) if action_type == "quarantine_duplicate_deliveries" else current_count + len(ids)
        preview = {"campaign_id": campaign_id, "campaign_name": campaign["name"], "event_date": event_date,
                   "action_type": action_type, "description": action_label, "affected_count": len(ids),
                   "delivery_ids": event_ids, "database_ids": ids,
                   "expected_attributed_conversions": expected_count}
        action_id = str(uuid4())
        conn.execute(
            "INSERT INTO remediation_actions(action_id,run_id,action_type,campaign_id,event_date,preview,status) VALUES (%s,%s,%s,%s,%s,%s::jsonb,'pending')",
            (action_id, run_id, action_type, campaign_id, target_day, __import__("json").dumps(preview)),
        )
        return {"awaiting_approval": True, "action_id": action_id, "preview": {k: v for k, v in preview.items() if k != "database_ids"}}


def apply_approved_action(action_id: str):
    with connection() as conn:
        action = conn.execute("SELECT * FROM remediation_actions WHERE action_id=%s FOR UPDATE", (action_id,)).fetchone()
        if not action:
            raise ValueError("Remediation action was not found")
        if action["status"] != "pending":
            raise ValueError("This remediation has already been decided")
        preview = dict(action["preview"])
        ids = preview["database_ids"]
        if action["action_type"] == "quarantine_duplicate_deliveries":
            changed = conn.execute("UPDATE event_deliveries SET status='quarantined' WHERE id=ANY(%s) AND status='accepted'", (ids,)).rowcount
        else:
            changed = conn.execute("UPDATE event_deliveries SET campaign_id=%s WHERE id=ANY(%s) AND campaign_id IS NULL", (action["campaign_id"], ids)).rowcount
        if changed != len(ids):
            raise ValueError("The event set changed after preview. No partial result was accepted; prepare a fresh investigation.")
        conn.execute("UPDATE remediation_actions SET status='applied',executed_at=now() WHERE action_id=%s", (action_id,))
        return {"action_id": action_id, "status": "applied", "changed_rows": changed,
                "campaign_id": action["campaign_id"], "event_date": action["event_date"].isoformat(),
                "action_type": action["action_type"], "delivery_ids": preview["delivery_ids"]}


def reject_action(action_id: str):
    with connection() as conn:
        row = conn.execute("UPDATE remediation_actions SET status='rejected' WHERE action_id=%s AND status='pending' RETURNING action_id", (action_id,)).fetchone()
        if not row:
            raise ValueError("No pending remediation was found")
        return {"action_id": action_id, "status": "rejected", "changed_rows": 0}


def set_fail_next_read():
    with connection() as conn:
        conn.execute("UPDATE runtime_flags SET flag_value=1 WHERE flag_name='fail_next_read'")


def reset_demo():
    with connection() as conn:
        conn.execute("TRUNCATE run_events, remediation_actions, runs, event_deliveries, runbooks, campaigns RESTART IDENTITY CASCADE")
        conn.execute("UPDATE runtime_flags SET flag_value=0 WHERE flag_name='fail_next_read'")
        # Seed function lives in db.py to keep the demo deterministic.
        from db import seed_demo_data
        seed_demo_data(conn)
