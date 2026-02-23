#!/usr/bin/env python3
"""Send a test webhook to rootly2zabbix with a valid HMAC-SHA256 signature."""
import argparse
import hashlib
import hmac
import json
import os
import sys
import time
from datetime import datetime, timezone

import requests
from dotenv import load_dotenv

load_dotenv()


def make_payload(event_type: str, zabbix_event_id: str) -> dict:
    title = f"Test incident [ZABBIX:{zabbix_event_id}]"
    now = datetime.now(timezone.utc).isoformat()

    if event_type == "incident.resolved":
        return {
            "event_type": "incident.resolved",
            "data": {"id": "test-001", "title": title, "severity": "high"},
            "previous_values": {},
        }
    if event_type == "incident.mitigated":
        return {
            "event_type": "incident.mitigated",
            "data": {"id": "test-001", "title": title},
            "previous_values": {},
        }
    if event_type == "incident.updated.ack":
        return {
            "event_type": "incident.updated",
            "data": {"id": "test-001", "title": title, "acknowledged_at": now},
            "previous_values": {"acknowledged_at": None},
            "actor": {"name": "Test User"},
        }
    if event_type == "incident.updated.unack":
        return {
            "event_type": "incident.updated",
            "data": {"id": "test-001", "title": title, "acknowledged_at": None},
            "previous_values": {"acknowledged_at": now},
        }
    if event_type == "incident.updated.severity":
        return {
            "event_type": "incident.updated",
            "data": {"id": "test-001", "title": title, "severity": "critical"},
            "previous_values": {"severity": "high"},
        }
    if event_type == "incident.updated.note":
        return {
            "event_type": "incident.updated",
            "data": {"id": "test-001", "title": title, "summary": "Test note from send_test_webhook.py"},
            "previous_values": {"summary": ""},
        }
    raise ValueError(f"Unknown event type: {event_type}")


def sign(secret: str, timestamp: int, body: bytes) -> str:
    signed = f"{timestamp}.".encode() + body
    return hmac.new(secret.encode(), signed, hashlib.sha256).hexdigest()


def main():
    parser = argparse.ArgumentParser(description="Send a signed test webhook to rootly2zabbix")
    parser.add_argument("--event-type", default="incident.updated.ack",
                        choices=["incident.resolved", "incident.mitigated",
                                 "incident.updated.ack", "incident.updated.unack",
                                 "incident.updated.severity", "incident.updated.note"])
    parser.add_argument("--zabbix-event-id", required=True, help="Zabbix event ID to embed")
    parser.add_argument("--url", default="http://localhost:5000/webhook")
    parser.add_argument("--wrong-secret", action="store_true", help="Use wrong secret (expect 401)")
    args = parser.parse_args()

    secret = os.environ.get("ROOTLY_WEBHOOK_SECRET", "")
    if not secret:
        print("ERROR: ROOTLY_WEBHOOK_SECRET not set", file=sys.stderr)
        sys.exit(1)
    if args.wrong_secret:
        secret = "wrong-secret"

    payload = make_payload(args.event_type, args.zabbix_event_id)
    body = json.dumps(payload, separators=(",", ":")).encode()
    ts = int(time.time())
    sig = sign(secret, ts, body)

    resp = requests.post(
        args.url,
        data=body,
        headers={
            "Content-Type": "application/json",
            "X-Rootly-Signature": f"t={ts},v1={sig}",
        },
        timeout=10,
    )

    print(f"HTTP {resp.status_code}")
    print(resp.text)
    sys.exit(0 if resp.status_code == 200 else 1)


if __name__ == "__main__":
    main()
