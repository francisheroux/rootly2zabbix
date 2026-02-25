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

## EC2 Deployment

### 1. Clone / update the code

```bash
cd /home/ubuntu
git clone https://github.com/your-org/rootly2zabbix.git
# or, on subsequent deploys:
cd /home/ubuntu/rootly2zabbix && git pull
```

### 2. Create a virtualenv and install dependencies

```bash
cd /home/ubuntu/rootly2zabbix
python3 -m venv venv
venv/bin/pip install -r requirements.txt gunicorn
```

### 3. Configure the environment

```bash
cp .env.example .env
# Edit .env with your ROOTLY_WEBHOOK_SECRET, ZABBIX_URL, and ZABBIX_TOKEN
nano .env
```

### 4. Install and start the systemd service

```bash
sudo cp rootly2zabbix.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable rootly2zabbix
sudo systemctl start rootly2zabbix
```

### 5. Open the AWS Security Group inbound rule

In the AWS console, add an inbound rule to the EC2 instance's security group:

| Type | Protocol | Port | Source |
|------|----------|------|--------|
| Custom TCP | TCP | 5000 | Rootly IP range (or `0.0.0.0/0` to allow all) |

### 6. Verify

```bash
# Service status
sudo systemctl status rootly2zabbix

# Health endpoint
curl http://localhost:5000/health
# → {"status": "ok"}

# Live logs
journalctl -u rootly2zabbix -f
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

### Enable "Allow Manual Close" to allow Resolving of Alerts

1. Go to **Data Collection → Template**
2. Select Your Template's Triggers → **Select your Trigger "Allow manual close" → Update**
   - (You can also multi select triggers for a Template and Mass Update them)

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

## Troubleshooting

### Resolve doesn't close the Zabbix problem

The service returns HTTP 200 immediately and processes the Zabbix call asynchronously, so Zabbix errors are **never visible to Rootly** — you must check the service logs.

**Most common cause:** the trigger does not have "Allow manual close" enabled.

Fix in Zabbix UI: **Configuration → Triggers → [edit trigger] → check "Allow manual close" → Update**

#### Discovered triggers (LLD / network discovery)

Discovered triggers are read-only — Zabbix will refuse to set `manual_close` even
for a super-admin and will return:

> "Cannot update manual_close for a discovered trigger"

When that happens the service falls back gracefully: it adds a comment
("Resolved in Rootly") without closing the problem, and logs a
`zabbix_close_failed_falling_back_to_ack` warning.

**Permanent fix:** enable "Allow Manual Close" on the **template** trigger, not the
discovered instance.

1. Go to **Configuration → Templates → [your template] → Triggers**
2. Open the trigger that matches the discovered one
3. Check **Allow manual close**
4. Click **Update**

All future discovered instances will inherit this flag automatically.

### How to inspect async errors

```bash
# systemd
journalctl -u rootly2zabbix -f

# gunicorn stdout — look for processing_error lines
grep '"event":"processing_error"' /var/log/rootly2zabbix.log
```

A failed close looks like:

```json
{"event": "processing_error", "error": "Cannot close problem: trigger does not allow manual closing", ...}
```

A successful close looks like:

```json
{"event": "zabbix_close", ...}
```

### HTTP 200 doesn't mean Zabbix succeeded

The service always returns 200 to Rootly immediately (before calling Zabbix) to prevent Rootly from disabling the webhook on transient failures. All Zabbix outcomes — success or error — are only visible in the service logs.

---

## Testing

```bash
# Run unit tests
pytest tests/

# Send a signed test webhook (ROOTLY_WEBHOOK_SECRET must be set in .env or environment)
python3 send_test_webhook.py --event-type incident.updated.ack    --zabbix-event-id 12345
python3 send_test_webhook.py --event-type incident.updated.unack  --zabbix-event-id 12345
python3 send_test_webhook.py --event-type incident.resolved       --zabbix-event-id 12345
python3 send_test_webhook.py --event-type incident.updated.severity --zabbix-event-id 12345
python3 send_test_webhook.py --event-type incident.mitigated      --zabbix-event-id 12345

# Watch logs to confirm Zabbix outcome
journalctl -u rootly2zabbix -f
```
