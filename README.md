# rootly2zabbix

Bidirectional Rootly ↔ Zabbix integration service. Receives Rootly webhook events and mirrors incident state changes (acknowledge, unacknowledge, severity, resolve, mitigate) back into Zabbix via the JSON-RPC API.

> **Direction handled here:** Rootly → Zabbix
> **Opposite direction** (Zabbix → Rootly): handled separately by the Zabbix webhook media type

---

## How It Works

```
Rootly webhook POST
    ↓
Flask /webhook  (HMAC-SHA256 signature verification)
    ↓
Event parser    (extract event type, incident data, Zabbix event ID)
    ↓
Event router    (map Rootly event → Zabbix action)
    ↓
Zabbix JSON-RPC event.acknowledge
```

HTTP 200 is returned to Rootly **before** calling Zabbix, so transient Zabbix failures never cause Rootly to disable the webhook.

---

## Event Mapping

| Rootly event | Condition | Zabbix action |
|---|---|---|
| `incident.updated` | `acknowledged_at` null → timestamp | Acknowledge + add comment |
| `incident.updated` | `acknowledged_at` timestamp → null | Unacknowledge + add comment |
| `incident.resolved` | event type | Close problem (if `ROOTLY_RESOLVE_CLOSES_ZABBIX=true`) |
| `incident.updated` | `severity` changed | Change severity (if `ROOTLY_SEVERITY_UPDATES_ZABBIX=true`) |
| `incident.updated` | `summary` changed | Add comment with new summary |
| `incident.mitigated` | event type | Add comment "Mitigated in Rootly" |

### Severity Mapping

| Rootly | Zabbix level | Zabbix label |
|---|---|---|
| `critical` | 5 | Disaster |
| `high` | 4 | High |
| `medium` | 3 | Average |
| `low` | 2 | Warning |
| `informational` | 1 | Information |
| (unknown) | 0 | Not classified |

Override via `ROOTLY_SEVERITY_MAP` (JSON string).

---

## Setup

### 1. Install dependencies

```bash
pip install -r requirements.txt
```

### 2. Configure environment

```bash
cp .env.example .env
# Edit .env with your values
```

Required variables:

| Variable | Description |
|---|---|
| `ROOTLY_WEBHOOK_SECRET` | Rootly webhook signing secret |
| `ZABBIX_URL` | Zabbix API URL (comma-separated for failover) |
| `ZABBIX_TOKEN` | Zabbix API authentication token |

See `.env.example` for all optional variables.

### 3. Run

```bash
python main.py
```

Or with gunicorn for production:

```bash
pip install gunicorn
gunicorn -w 4 -b 0.0.0.0:5000 'main:app'
```

### 4. Health check

```bash
curl http://localhost:5000/health
# {"status": "ok"}
```

---

## Zabbix Setup

### Create API token

1. Go to **Administration → API tokens**
2. Click **Create API token**
3. Assign the token to a user with at minimum:
   - **Read** access to the host groups you want to sync
   - **Acknowledge** permission on the relevant triggers
4. Copy the token into `ZABBIX_TOKEN`

---

## Rootly Setup

### Configure the webhook

1. Go to **Rootly → Settings → Webhooks**
2. Click **Add Webhook**
3. Set **URL** to `https://your-server:5000/webhook`
4. Enable these events: `incident.updated`, `incident.resolved`, `incident.mitigated`
5. Copy the **Signing Secret** into `ROOTLY_WEBHOOK_SECRET`

### Pass the Zabbix Event ID

The Zabbix `{EVENT.ID}` must be embedded in the Rootly incident so this service can look it up. Options (tried in order):

**Option A — Custom field (recommended)**

1. Create a custom field named `zabbix_event_id` in Rootly
2. In your Zabbix webhook media type, set this field when creating the incident

**Option B — Label**

Include a label `zabbix_eventid:<EVENT.ID>` when creating the incident from Zabbix.

**Option C — Title**

Include `[ZABBIX:<EVENT.ID>]` in the incident title (e.g. `DB down [ZABBIX:42]`).

**Option D — Custom path**

Set `ZABBIX_EVENTID_PATH=some.dot.path` to extract from an arbitrary location in the payload.

---

## Testing

```bash
# Run unit tests
pytest tests/

# Send a test webhook (replace SECRET and payload as needed)
TIMESTAMP=$(date +%s)
BODY='{"event_type":"incident.resolved","data":{"id":"abc","title":"Test [ZABBIX:12345]"},"previous_values":{}}'
SIG="t=${TIMESTAMP},v1=$(echo -n "${TIMESTAMP}.${BODY}" | openssl dgst -sha256 -hmac "$ROOTLY_WEBHOOK_SECRET" -hex | awk '{print $2}')"
curl -X POST http://localhost:5000/webhook \
  -H "Content-Type: application/json" \
  -H "X-Rootly-Signature: ${SIG}" \
  -d "$BODY"
```
