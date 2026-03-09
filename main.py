import hmac
import json
import logging
import threading
import time

from flask import Flask, jsonify, request

from config import load_config
from rootly import parse_event, verify_signature
from zabbix import (
    ACTION_ACKNOWLEDGE,
    ACTION_CLOSE,
    ACTION_MESSAGE,
    ACTION_SEVERITY,
    ACTION_SUPPRESS,
    ACTION_UNACKNOWLEDGE,
    ZabbixAPIError,
    ZabbixClient,
)

# ---------------------------------------------------------------------------
# Logging — structured JSON lines
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format='{"time":"%(asctime)s","level":"%(levelname)s","logger":"%(name)s","message":%(message)s}',
)
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Config + clients (module-level so they are reused across requests)
# ---------------------------------------------------------------------------
config = load_config()

if config.debug:
    logging.getLogger().setLevel(logging.DEBUG)

zabbix = ZabbixClient(urls=config.zabbix_urls, token=config.zabbix_token)

# ---------------------------------------------------------------------------
# Flask app
# ---------------------------------------------------------------------------
app = Flask(__name__)


@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "ok"})


@app.route("/webhook", methods=["POST"])
def webhook():
    raw_body = request.get_data()
    sig_header = request.headers.get("X-Rootly-Signature", "")

    if not verify_signature(raw_body, sig_header, config.rootly_webhook_secret):
        logger.warning('"Webhook signature verification failed"')
        return jsonify({"error": "Invalid signature"}), 401

    try:
        payload = json.loads(raw_body)
    except json.JSONDecodeError:
        return jsonify({"error": "Invalid JSON"}), 400

    # Process asynchronously so we can return 200 immediately.
    # This prevents Rootly from disabling the webhook on transient Zabbix failures.
    t = threading.Thread(target=_process_event, args=(payload,), daemon=True)
    t.start()

    return jsonify({"status": "accepted"}), 200


@app.route("/acknowledge", methods=["POST"])
def acknowledge_webhook():
    api_key = request.headers.get("X-API-Key", "")
    if not hmac.compare_digest(api_key, config.rootly_webhook_secret):
        return jsonify({"error": "Unauthorized"}), 401

    payload = request.get_json(silent=True)
    if not payload:
        return jsonify({"error": "Invalid JSON"}), 400

    zabbix_event_id = payload.get("zabbix_event_id")
    message = payload.get("message", "Acknowledged in Rootly")

    if not zabbix_event_id:
        return jsonify({"error": "zabbix_event_id required"}), 400

    def _process():
        try:
            zabbix.acknowledge(zabbix_event_id, message, ACTION_ACKNOWLEDGE | ACTION_MESSAGE)
            logger.info(json.dumps({"event": "zabbix_ack", "zabbix_event_id": zabbix_event_id}))
        except Exception as e:  # pylint: disable=broad-except
            logger.error(json.dumps({"event": "ack_error", "zabbix_event_id": zabbix_event_id, "error": str(e)}))

    threading.Thread(target=_process, daemon=True).start()
    return jsonify({"status": "accepted"}), 202


@app.route("/resolve", methods=["POST"])
def resolve_webhook():
    api_key = request.headers.get("X-API-Key", "")
    if not hmac.compare_digest(api_key, config.rootly_webhook_secret):
        return jsonify({"error": "Unauthorized"}), 401

    payload = request.get_json(silent=True)
    if not payload:
        return jsonify({"error": "Invalid JSON"}), 400

    zabbix_event_id = payload.get("zabbix_event_id")
    message = payload.get("message", "Resolved in Rootly")

    if not zabbix_event_id:
        return jsonify({"error": "zabbix_event_id required"}), 400

    def _process():
        try:
            _resolve_zabbix_event(zabbix_event_id, message)
        except Exception as e:  # pylint: disable=broad-except
            logger.error(json.dumps({"event": "resolve_error", "zabbix_event_id": zabbix_event_id, "error": str(e)}))

    threading.Thread(target=_process, daemon=True).start()
    return jsonify({"status": "accepted"}), 202


# ---------------------------------------------------------------------------
# Event processing (runs in background thread)
# ---------------------------------------------------------------------------

def _process_event(payload: dict) -> None:
    try:
        event = parse_event(payload, config.zabbix_eventid_path)
        logger.info(
            json.dumps(
                {
                    "event": "processing",
                    "event_type": event.event_type,
                    "incident_id": event.incident_id,
                    "zabbix_event_id": event.zabbix_event_id,
                }
            )
        )

        if not event.zabbix_event_id:
            logger.warning(
                json.dumps(
                    {
                        "event": "no_zabbix_eventid",
                        "incident_id": event.incident_id,
                        "event_type": event.event_type,
                    }
                )
            )
            return

        _route_event(event)

    except Exception as exc:  # pylint: disable=broad-except
        logger.error(json.dumps({"event": "processing_error", "error": str(exc)}))


def _route_event(event) -> None:
    """Dispatch a parsed Rootly event to the appropriate Zabbix handler."""
    et = event.event_type
    prev = event.previous_values

    if et == "incident.resolved":
        if config.rootly_resolve_closes_zabbix:
            _handle_resolved(event)
        return

    if et == "incident.updated":
        ack_at_prev = prev.get("acknowledged_at")
        ack_at_curr = (event.raw_payload.get("data") or {}).get("acknowledged_at")

        if ack_at_prev is None and ack_at_curr is not None:
            _handle_acknowledged(event)
        elif ack_at_prev is not None and ack_at_curr is None:
            _handle_unacknowledged(event)
        elif "severity" in prev and config.rootly_severity_updates_zabbix:
            _handle_severity_change(event)
        elif "summary" in prev or event.note:
            _handle_note_added(event)
        else:
            logger.info(json.dumps({"event": "no_action", "event_type": et}))
        return

    logger.info(json.dumps({"event": "unhandled_event_type", "event_type": et}))


# ---------------------------------------------------------------------------
# Individual handlers
# ---------------------------------------------------------------------------

def _resolve_zabbix_event(zabbix_event_id: str, message: str) -> None:
    """Resolve a Zabbix event: suppress if the trigger has manual_close=1, otherwise close."""
    trigger_id: str | None = None
    manual_close: str = "0"

    try:
        event_details = zabbix.get_event(zabbix_event_id)
        trigger_id = event_details.get("objectid")
        if trigger_id:
            trigger = zabbix.get_trigger(trigger_id)
            manual_close = trigger.get("manual_close", "0")
    except ZabbixAPIError as e:
        logger.warning(json.dumps({
            "event": "trigger_lookup_failed",
            "hint": "Falling back to close action",
            "error": str(e),
        }))

    if manual_close == "1":
        suppress_until = int(time.time()) + config.rootly_suppress_duration_days * 86400
        zabbix.acknowledge(
            zabbix_event_id,
            message=message,
            action=ACTION_SUPPRESS | ACTION_MESSAGE,
            suppress_until=suppress_until,
        )
        logger.info(json.dumps({
            "event": "zabbix_suppress",
            "zabbix_event_id": zabbix_event_id,
            "trigger_id": trigger_id,
            "suppress_until": suppress_until,
            "suppress_duration_days": config.rootly_suppress_duration_days,
        }))
    else:
        try:
            zabbix.acknowledge(zabbix_event_id, message=message, action=ACTION_CLOSE | ACTION_MESSAGE)
            logger.info(json.dumps({"event": "zabbix_close", "zabbix_event_id": zabbix_event_id}))
        except ZabbixAPIError as e:
            logger.warning(json.dumps({
                "event": "zabbix_close_failed_falling_back_to_ack",
                "hint": 'Enable "Allow Manual Close" on the template trigger in Zabbix',
                "error": str(e),
            }))
            fallback_msg = message + ' — unable to close in Zabbix. Enable "Allow Manual Close" on this trigger.'
            zabbix.acknowledge(zabbix_event_id, message=fallback_msg, action=ACTION_ACKNOWLEDGE | ACTION_MESSAGE)


def _handle_resolved(event) -> None:
    msg = "Resolved in Rootly"
    if event.incident_id:
        msg += f" (incident #{event.incident_id})"

    _resolve_zabbix_event(event.zabbix_event_id, msg)


def _handle_acknowledged(event) -> None:
    acknowledger = event.acknowledger or "Unknown"
    msg = f"Acknowledged in Rootly by {acknowledger}"
    logger.info(
        json.dumps(
            {
                "event": "zabbix_ack",
                "zabbix_event_id": event.zabbix_event_id,
                "acknowledger": acknowledger,
            }
        )
    )
    zabbix.acknowledge(event.zabbix_event_id, message=msg, action=ACTION_ACKNOWLEDGE | ACTION_MESSAGE)


def _handle_unacknowledged(event) -> None:
    msg = "Unacknowledged in Rootly"
    logger.info(json.dumps({"event": "zabbix_unack", "zabbix_event_id": event.zabbix_event_id}))
    zabbix.acknowledge(event.zabbix_event_id, message=msg, action=ACTION_UNACKNOWLEDGE | ACTION_MESSAGE)


def _handle_severity_change(event) -> None:
    new_severity = event.severity or ""
    zabbix_severity = config.severity_map.get(new_severity.lower(), 0)
    msg = f"Severity changed to {new_severity} in Rootly"
    logger.info(
        json.dumps(
            {
                "event": "zabbix_severity",
                "zabbix_event_id": event.zabbix_event_id,
                "rootly_severity": new_severity,
                "zabbix_severity": zabbix_severity,
            }
        )
    )
    zabbix.acknowledge(
        event.zabbix_event_id,
        message=msg,
        action=ACTION_SEVERITY | ACTION_MESSAGE,
        severity=zabbix_severity,
    )


def _handle_note_added(event) -> None:
    note = event.note or "Updated in Rootly"
    logger.info(json.dumps({"event": "zabbix_comment", "zabbix_event_id": event.zabbix_event_id}))
    zabbix.acknowledge(event.zabbix_event_id, message=note, action=ACTION_MESSAGE)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=config.port, debug=config.debug)
