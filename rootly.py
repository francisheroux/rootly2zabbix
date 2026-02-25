import hashlib
import hmac
import logging
import re
import time
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


@dataclass
class RootlyEvent:
    event_type: str
    incident_id: str | None
    zabbix_event_id: str | None
    acknowledger: str | None
    severity: str | None
    previous_severity: str | None
    note: str | None
    previous_values: dict
    raw_payload: dict


def verify_signature(raw_body: bytes, signature_header: str, secret: str) -> bool:
    """Verify a Rootly webhook signature.

    Header format: t=<timestamp>,v1=<hmac_sha256>

    Verification steps:
    1. Extract t (timestamp) and v1 (signature) from header
    2. Reject if timestamp is >5 minutes old (replay attack prevention)
    3. Compute HMAC-SHA256(secret, "{t}{raw_body}")
    4. Compare using constant-time comparison
    """
    if not signature_header:
        return False

    parts: dict[str, str] = {}
    for part in signature_header.split(","):
        part = part.strip()
        if "=" in part:
            k, v = part.split("=", 1)
            parts[k.strip()] = v.strip()

    timestamp = parts.get("t")
    v1 = parts.get("v1")

    if not timestamp or not v1:
        logger.warning("Signature header missing t or v1")
        return False

    try:
        ts = int(timestamp)
    except ValueError:
        logger.warning("Signature header has non-integer timestamp")
        return False

    age = abs(time.time() - ts)
    if age > 300:
        logger.warning("Signature timestamp is %.0fs old (>300s)", age)
        return False

    signed_content = (timestamp + raw_body.decode('utf-8', errors='replace')).encode("utf-8")
    expected = hmac.new(secret.encode("utf-8"), signed_content, hashlib.sha256).hexdigest()

    return hmac.compare_digest(expected, v1)


def _dotpath_get(obj: dict, path: str):
    """Navigate a dot-notation path in a nested dict. Returns None if any key is missing."""
    current = obj
    for part in path.split("."):
        if isinstance(current, dict):
            current = current.get(part)
        else:
            return None
    return current


def extract_zabbix_event_id(incident: dict, custom_path: str | None = None) -> str | None:
    """Try multiple paths to extract the Zabbix event ID from an incident dict.

    Extraction order:
    1. Custom path (ZABBIX_EVENTID_PATH env var, dot-notation)
    2. data.custom_fields.zabbix_event_id
    3. data.labels array — label matching zabbix_eventid:(\\d+)
    4. data.title — regex [ZABBIX:(\\d+)]
    """
    # 1. Custom path from config
    if custom_path:
        result = _dotpath_get(incident, custom_path)
        if result is not None:
            return str(result)

    # 2. Custom field: custom_fields.zabbix_event_id
    custom_fields = incident.get("custom_fields") or {}
    if isinstance(custom_fields, dict):
        val = custom_fields.get("zabbix_event_id")
        if val:
            return str(val)

    # 3. Labels array
    labels = incident.get("labels") or []
    if isinstance(labels, list):
        for label in labels:
            label_str = label.get("name", "") if isinstance(label, dict) else str(label)
            match = re.search(r"zabbix_eventid:(\d+)", label_str, re.IGNORECASE)
            if match:
                return match.group(1)

    # 4. Title regex
    title = incident.get("title") or incident.get("name") or ""
    if title:
        match = re.search(r"\[ZABBIX:(\d+)\]", title, re.IGNORECASE)
        if match:
            return match.group(1)

    return None


def parse_event(payload: dict, custom_eventid_path: str | None = None) -> RootlyEvent:
    """Parse a Rootly webhook payload into a RootlyEvent dataclass."""
    event_type = payload.get("event_type", "")
    data = payload.get("data") or {}
    previous_values = payload.get("previous_values") or {}

    incident_id = data.get("id")

    # Acknowledger from actor field (various possible locations)
    acknowledger: str | None = None
    actor = payload.get("actor") or payload.get("user") or {}
    if isinstance(actor, dict):
        acknowledger = (
            actor.get("name")
            or actor.get("full_name")
            or actor.get("email")
        )

    severity = data.get("severity")
    previous_severity = previous_values.get("severity")
    note = data.get("summary") or data.get("message")

    zabbix_event_id = extract_zabbix_event_id(data, custom_eventid_path)

    return RootlyEvent(
        event_type=event_type,
        incident_id=str(incident_id) if incident_id else None,
        zabbix_event_id=zabbix_event_id,
        acknowledger=acknowledger,
        severity=severity,
        previous_severity=previous_severity,
        note=note,
        previous_values=previous_values,
        raw_payload=payload,
    )
