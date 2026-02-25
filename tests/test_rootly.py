import hashlib
import hmac
import json
import time
from pathlib import Path

import pytest

from rootly import extract_zabbix_event_id, parse_event, verify_signature

FIXTURES = json.loads(
    (Path(__file__).parent / "fixtures" / "webhook_payloads.json").read_text()
)
SECRET = "test_secret_key"


def make_signature(body: bytes, secret: str, timestamp: int | None = None) -> str:
    ts = timestamp if timestamp is not None else int(time.time())
    signed = (str(ts) + body.decode('utf-8')).encode("utf-8")
    digest = hmac.new(secret.encode("utf-8"), signed, hashlib.sha256).hexdigest()
    return f"t={ts},v1={digest}"


class TestVerifySignature:
    def test_valid_signature(self):
        body = b'{"test": "payload"}'
        sig = make_signature(body, SECRET)
        assert verify_signature(body, sig, SECRET) is True

    def test_wrong_secret(self):
        body = b'{"test": "payload"}'
        sig = make_signature(body, "wrong_secret")
        assert verify_signature(body, sig, SECRET) is False

    def test_tampered_body(self):
        body = b'{"test": "payload"}'
        sig = make_signature(body, SECRET)
        tampered = b'{"test": "tampered"}'
        assert verify_signature(tampered, sig, SECRET) is False

    def test_expired_timestamp(self):
        body = b'{"test": "payload"}'
        old_ts = int(time.time()) - 400  # >5 minutes ago
        sig = make_signature(body, SECRET, timestamp=old_ts)
        assert verify_signature(body, sig, SECRET) is False

    def test_missing_header(self):
        body = b'{"test": "payload"}'
        assert verify_signature(body, "", SECRET) is False

    def test_malformed_header(self):
        body = b'{"test": "payload"}'
        assert verify_signature(body, "not-a-valid-header", SECRET) is False

    def test_missing_v1(self):
        body = b'{"test": "payload"}'
        ts = int(time.time())
        assert verify_signature(body, f"t={ts}", SECRET) is False

    def test_header_with_spaces(self):
        """Rootly may include spaces after commas: t=..., v1=..."""
        body = b'{"test": "payload"}'
        ts = int(time.time())
        signed = (str(ts) + body.decode('utf-8')).encode("utf-8")
        digest = hmac.new(SECRET.encode("utf-8"), signed, hashlib.sha256).hexdigest()
        sig = f"t={ts}, v1={digest}"  # note the space
        assert verify_signature(body, sig, SECRET) is True


class TestExtractZabbixEventId:
    def test_from_title(self):
        incident = {"title": "Database is down [ZABBIX:12345]"}
        assert extract_zabbix_event_id(incident) == "12345"

    def test_from_title_case_insensitive(self):
        incident = {"title": "DB down [zabbix:99]"}
        assert extract_zabbix_event_id(incident) == "99"

    def test_from_custom_field(self):
        incident = {"custom_fields": {"zabbix_event_id": "99999"}}
        assert extract_zabbix_event_id(incident) == "99999"

    def test_from_labels(self):
        incident = {"labels": [{"name": "team:infra"}, {"name": "zabbix_eventid:77777"}]}
        assert extract_zabbix_event_id(incident) == "77777"

    def test_from_labels_case_insensitive(self):
        incident = {"labels": [{"name": "Zabbix_EventId:88888"}]}
        assert extract_zabbix_event_id(incident) == "88888"

    def test_from_custom_path(self):
        incident = {"metadata": {"zabbix": {"event_id": "55555"}}}
        assert extract_zabbix_event_id(incident, "metadata.zabbix.event_id") == "55555"

    def test_custom_path_takes_priority(self):
        """Custom path should be tried before built-in paths."""
        incident = {
            "title": "[ZABBIX:11111]",
            "override_id": "22222",
        }
        assert extract_zabbix_event_id(incident, "override_id") == "22222"

    def test_no_eventid(self):
        incident = {"title": "No zabbix reference here"}
        assert extract_zabbix_event_id(incident) is None

    def test_empty_incident(self):
        assert extract_zabbix_event_id({}) is None

    def test_custom_field_numeric_value(self):
        """Custom fields may store numeric values; should be stringified."""
        incident = {"custom_fields": {"zabbix_event_id": 42}}
        assert extract_zabbix_event_id(incident) == "42"


class TestParseEvent:
    def test_parse_acknowledged(self):
        payload = FIXTURES["incident_updated_acknowledged"]
        event = parse_event(payload)
        assert event.event_type == "incident.updated"
        assert event.zabbix_event_id == "12345"
        assert event.acknowledger == "John Doe"
        assert event.previous_values.get("acknowledged_at") is None
        assert event.incident_id == "abc123"

    def test_parse_unacknowledged(self):
        payload = FIXTURES["incident_updated_unacknowledged"]
        event = parse_event(payload)
        assert event.acknowledger == "Jane Smith"
        assert event.previous_values.get("acknowledged_at") is not None

    def test_parse_resolved(self):
        payload = FIXTURES["incident_resolved"]
        event = parse_event(payload)
        assert event.event_type == "incident.resolved"
        assert event.zabbix_event_id == "12345"

    def test_parse_severity_change(self):
        payload = FIXTURES["incident_updated_severity"]
        event = parse_event(payload)
        assert event.severity == "critical"
        assert event.previous_severity == "high"

    def test_parse_note_added(self):
        payload = FIXTURES["incident_updated_note"]
        event = parse_event(payload)
        assert event.note == "We found the root cause"

    def test_parse_mitigated(self):
        payload = FIXTURES["incident_mitigated"]
        event = parse_event(payload)
        assert event.event_type == "incident.mitigated"
        assert event.zabbix_event_id == "12345"

    def test_parse_no_eventid(self):
        payload = FIXTURES["incident_no_eventid"]
        event = parse_event(payload)
        assert event.zabbix_event_id is None

    def test_parse_custom_field_eventid(self):
        payload = FIXTURES["incident_with_custom_field"]
        event = parse_event(payload)
        assert event.zabbix_event_id == "99999"

    def test_parse_label_eventid(self):
        payload = FIXTURES["incident_with_label"]
        event = parse_event(payload)
        assert event.zabbix_event_id == "77777"

    def test_parse_empty_payload(self):
        event = parse_event({})
        assert event.event_type == ""
        assert event.incident_id is None
        assert event.zabbix_event_id is None
