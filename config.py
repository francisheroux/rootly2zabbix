import json
import os
import sys
from dataclasses import dataclass, field

from dotenv import load_dotenv

DEFAULT_SEVERITY_MAP: dict[str, int] = {
    "critical": 5,
    "high": 4,
    "medium": 3,
    "low": 2,
    "informational": 1,
}


@dataclass
class Config:
    rootly_webhook_secret: str
    zabbix_urls: list[str]
    zabbix_token: str
    zabbix_eventid_path: str | None
    rootly_api_token: str | None
    rootly_resolve_closes_zabbix: bool
    rootly_severity_updates_zabbix: bool
    severity_map: dict[str, int]
    port: int
    debug: bool


def load_config() -> Config:
    load_dotenv()

    missing: list[str] = []

    rootly_webhook_secret = os.environ.get("ROOTLY_WEBHOOK_SECRET", "")
    if not rootly_webhook_secret:
        missing.append("ROOTLY_WEBHOOK_SECRET")

    zabbix_url = os.environ.get("ZABBIX_URL", "")
    if not zabbix_url:
        missing.append("ZABBIX_URL")

    zabbix_token = os.environ.get("ZABBIX_TOKEN", "")
    if not zabbix_token:
        missing.append("ZABBIX_TOKEN")

    if missing:
        print(f"ERROR: Missing required environment variables: {', '.join(missing)}", file=sys.stderr)
        sys.exit(1)

    zabbix_urls = [u.strip() for u in zabbix_url.split(",") if u.strip()]

    severity_map = dict(DEFAULT_SEVERITY_MAP)
    raw_severity_map = os.environ.get("ROOTLY_SEVERITY_MAP", "")
    if raw_severity_map:
        try:
            override = json.loads(raw_severity_map)
            severity_map.update(override)
        except json.JSONDecodeError as e:
            print(f"WARNING: ROOTLY_SEVERITY_MAP is not valid JSON: {e}", file=sys.stderr)

    return Config(
        rootly_webhook_secret=rootly_webhook_secret,
        zabbix_urls=zabbix_urls,
        zabbix_token=zabbix_token,
        zabbix_eventid_path=os.environ.get("ZABBIX_EVENTID_PATH"),
        rootly_api_token=os.environ.get("ROOTLY_API_TOKEN"),
        rootly_resolve_closes_zabbix=os.environ.get("ROOTLY_RESOLVE_CLOSES_ZABBIX", "true").lower() == "true",
        rootly_severity_updates_zabbix=os.environ.get("ROOTLY_SEVERITY_UPDATES_ZABBIX", "true").lower() == "true",
        severity_map=severity_map,
        port=int(os.environ.get("PORT", "5000")),
        debug=os.environ.get("DEBUG", "false").lower() == "true",
    )
