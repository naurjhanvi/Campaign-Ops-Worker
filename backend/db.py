import json
import os
from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone

import psycopg
from psycopg.rows import dict_row


DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://worker:worker@localhost:5432/campaign_ops")
SCHEMA_PATH = os.path.join(os.path.dirname(__file__), "schema.sql")


def connect():
    return psycopg.connect(DATABASE_URL, row_factory=dict_row)


@contextmanager
def connection():
    conn = connect()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db():
    with open(SCHEMA_PATH, encoding="utf-8") as f:
        schema = f.read()
    with connection() as conn:
        for statement in schema.split(";"):
            if statement.strip():
                conn.execute(statement)
        conn.execute("INSERT INTO runtime_flags(flag_name, flag_value) VALUES ('fail_next_read', 0) ON CONFLICT DO NOTHING")
        count = conn.execute("SELECT count(*) AS n FROM campaigns").fetchone()["n"]
        if count == 0:
            seed_demo_data(conn)


def seed_demo_data(conn):
    campaigns = [
        ("CMP-101", "Autumn Launch", "autumn_launch", "Email"),
        ("CMP-202", "Creator Referral", "creator_referral", "Social"),
        ("CMP-303", "Northstar Retargeting", "northstar_retargeting", "Paid search"),
    ]
    with conn.cursor() as cur:
        cur.executemany(
            "INSERT INTO campaigns(campaign_id, name, source_code, channel) VALUES (%s,%s,%s,%s)",
            campaigns,
        )
    day = date(2026, 10, 4)
    start = datetime(day.year, day.month, day.day, 9, 0, tzinfo=timezone.utc)
    events = []
    # Autumn Launch has 20 genuine conversions and 3 repeated deliveries.
    for i in range(1, 21):
        key = f"autumn-order-{i:03d}"
        occurred = start + timedelta(minutes=i * 11)
        events.append((f"del-autumn-{i:03d}", key, "CMP-101", "autumn_launch", "conversion", f"user-{i:03d}", occurred, occurred + timedelta(seconds=4), "accepted", {"source": "webhook", "order_ref": key}))
    for i in (3, 9, 13):
        key = f"autumn-order-{i:03d}"
        occurred = start + timedelta(minutes=i * 11)
        events.append((f"del-autumn-retry-{i:03d}", key, "CMP-101", "autumn_launch", "conversion", f"user-{i:03d}", occurred, occurred + timedelta(minutes=2), "accepted", {"source": "webhook-retry", "order_ref": key}))
    # Creator Referral has 10 attributed conversions plus 3 with a missing campaign ID.
    for i in range(1, 11):
        key = f"creator-order-{i:03d}"
        occurred = start + timedelta(hours=2, minutes=i * 13)
        events.append((f"del-creator-{i:03d}", key, "CMP-202", "creator_referral", "conversion", f"creator-user-{i:03d}", occurred, occurred + timedelta(seconds=5), "accepted", {"source": "webhook", "order_ref": key}))
    for i in range(11, 14):
        key = f"creator-order-{i:03d}"
        occurred = start + timedelta(hours=2, minutes=i * 13)
        events.append((f"del-creator-unmapped-{i:03d}", key, None, "creator_referral", "conversion", f"creator-user-{i:03d}", occurred, occurred + timedelta(seconds=5), "accepted", {"source": "webhook", "order_ref": key}))
    # A clean control campaign makes the dashboard and comparison useful.
    for i in range(1, 16):
        key = f"northstar-order-{i:03d}"
        occurred = start + timedelta(hours=5, minutes=i * 9)
        events.append((f"del-northstar-{i:03d}", key, "CMP-303", "northstar_retargeting", "conversion", f"retarget-user-{i:03d}", occurred, occurred + timedelta(seconds=3), "accepted", {"source": "webhook", "order_ref": key}))
    with conn.cursor() as cur:
        cur.executemany(
            """INSERT INTO event_deliveries
            (delivery_id,idempotency_key,campaign_id,source_campaign_code,event_type,user_id,occurred_at,received_at,status,payload)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
            [(*row[:9], json.dumps(row[9])) for row in events],
        )
        cur.executemany(
            "INSERT INTO runbooks(runbook_id,title,body) VALUES (%s,%s,%s)",
            [
                ("RB-DUPLICATE", "Repeated conversion deliveries", "Investigate an unexpected conversion increase by grouping accepted conversion deliveries by idempotency_key within the same campaign and event date. A delivery with the same key received later is a retry, not a second conversion. Preserve the earliest accepted delivery and quarantine later repeats. Preview the affected delivery IDs and obtain operator approval before changing statuses. Recompute the daily count from accepted events and verify it equals the number of unique order keys."),
                ("RB-ATTRIBUTION", "Missing campaign attribution", "Investigate a conversion drop by finding accepted conversion events with a NULL campaign_id. Map source_campaign_code to the campaigns table. Only repair events when the source code maps to exactly one campaign and the event date is in scope. Show the event IDs and target campaign before asking for approval. Recompute metrics and verify attributed counts after applying the change."),
                ("RB-AMBIGUOUS", "Ambiguous source mapping", "Never assign a campaign when the source campaign code is missing or maps to multiple campaigns. Collect the affected event IDs and ask an operator for clarification. Do not apply a best guess."),
            ],
        )


def json_value(value):
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return value


def consume_transient_failure(conn):
    row = conn.execute("SELECT flag_value FROM runtime_flags WHERE flag_name='fail_next_read' FOR UPDATE").fetchone()
    if row and row["flag_value"]:
        conn.execute("UPDATE runtime_flags SET flag_value=0 WHERE flag_name='fail_next_read'")
        return True
    return False
