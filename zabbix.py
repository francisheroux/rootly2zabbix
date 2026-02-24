import logging
import time
import uuid

import requests

logger = logging.getLogger(__name__)

# Action bitmasks for event.acknowledge
ACTION_CLOSE = 1
ACTION_ACKNOWLEDGE = 2
ACTION_MESSAGE = 4
ACTION_SEVERITY = 16
ACTION_UNACKNOWLEDGE = 8


class ZabbixAPIError(Exception):
    """Raised when the Zabbix API returns an error response."""


class ZabbixConnectionError(Exception):
    """Raised when all Zabbix URLs are unreachable after retries."""


class ZabbixClient:
    """Zabbix JSON-RPC 2.0 client with multi-URL failover and retry logic."""

    def __init__(
        self,
        urls: list[str],
        token: str,
        retries: int = 3,
        backoff: float = 1.0,
    ) -> None:
        self.urls = urls
        self.token = token
        self.retries = retries
        self.backoff = backoff
        self.session = requests.Session()
        self.session.headers.update(
            {
                "Content-Type": "application/json-rpc",
                "Authorization": f"Bearer {token}",
            }
        )

    def get_event(self, event_id: str) -> dict:
        """Fetch event details including objectid (trigger ID).

        Returns the first matching event dict, or {} if not found.
        """
        events = self._call("event.get", {
            "eventids": [event_id],
            "output": ["eventid", "objectid"],
        })
        return events[0] if events else {}

    def enable_trigger_manual_close(self, trigger_id: str) -> dict:
        """Enable Allow Manual Close on a trigger.

        Requires the API user to have Admin or Super admin role in Zabbix.
        Raises ZabbixAPIError if the user lacks sufficient permissions.
        """
        return self._call("trigger.update", {
            "triggerid": trigger_id,
            "manual_close": 1,
        })

    def acknowledge(
        self,
        event_id: str,
        message: str = "",
        action: int = ACTION_MESSAGE,
        severity: int | None = None,
    ) -> dict:
        """Call event.acknowledge on the Zabbix API.

        Args:
            event_id: Zabbix event ID to acknowledge.
            message: Message/comment to attach (required when action includes MESSAGE).
            action: Bitmask of actions (ACTION_* constants, OR'd together).
            severity: New severity level (0-5); only used when action includes ACTION_SEVERITY.
        """
        params: dict = {
            "eventids": [event_id],
            "action": action,
        }
        if message:
            params["message"] = message
        if severity is not None and (action & ACTION_SEVERITY):
            params["severity"] = severity

        return self._call("event.acknowledge", params)

    def _call(self, method: str, params: dict) -> dict:
        """Make a JSON-RPC call with per-URL retry and failover to next URL."""
        payload = {
            "jsonrpc": "2.0",
            "method": method,
            "params": params,
            "id": str(uuid.uuid4()),
        }

        last_exception: Exception | None = None

        for url in self.urls:
            for attempt in range(self.retries):
                try:
                    response = self.session.post(url, json=payload, timeout=10)
                    response.raise_for_status()
                    body = response.json()

                    if "error" in body:
                        err = body["error"]
                        raise ZabbixAPIError(
                            f"{err.get('message', 'Unknown error')}: {err.get('data', '')}"
                        )

                    return body.get("result", {})

                except ZabbixAPIError:
                    raise  # API-level errors are not transient; don't retry
                except Exception as exc:
                    last_exception = exc
                    wait = self.backoff * (2**attempt)
                    logger.warning(
                        "Zabbix request to %s attempt %d/%d failed: %s; retrying in %.1fs",
                        url,
                        attempt + 1,
                        self.retries,
                        exc,
                        wait,
                    )
                    if attempt < self.retries - 1:
                        time.sleep(wait)

        raise ZabbixConnectionError(
            f"All Zabbix URLs failed. Last error: {last_exception}"
        ) from last_exception
