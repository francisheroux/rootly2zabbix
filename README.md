# rootly2zabbix

Receives Rootly webhook events and mirrors incident state changes (acknowledge, unacknowledge, severity, resolve) back into Zabbix via the Zabbix API.

## Setup

### 1. Clone / update the code

```bash
cd /home/ubuntu/
git clone https://github.com/francisheroux/rootly2zabbix.git
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

### 5. Whitelist Rootly IP Addresses

Make sure you have whitelisted the Rootly IP's (TCP Port 5000) in your inbound rules for where your Zabbix Server is hosted

https://docs.rootly.com/integrations/ip-whitelist

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

## Zabbix Setup

### Create API token

1. Go to **Users → API tokens**
2. Click **Create API token**
3. Assign the token to a user (normally the same user as your Rootly Media Type) with at minimum:
   - **Read** access to the host groups you want to sync
   - **Acknowledge** permission on the relevant triggers
4. Copy the generated token (this is your `ZABBIX_TOKEN` for your `.env` file)

### Enable "Allow Manual Close" to allow Resolving of Alerts

1. Go to **Data Collection → Templates**
2. Select your template → **Triggers** → open the trigger
3. Check **Allow manual close** → **Update**
   - You can also multi-select triggers and use **Mass Update**

---

## Rootly Setup

### Configure the webhook

1. Go to **Rootly → Configuration → Webhooks**
2. Click **New Endpoint**
3. Give it a **Title** (e.g. "rootly2zabbix-webhook")
4. Set **URL** to `https://your-server:5000/webhook`
5. Copy the **Secret** — this is your `ROOTLY_WEBHOOK_SECRET` for `.env`
6. Add these **Event Triggers**: `incident.updated` and `incident.resolved`

### Pass the Zabbix Event ID

This service needs to know which Zabbix event to update when a Rootly webhook arrives. The Zabbix `{EVENT.ID}` must travel from Zabbix into the Rootly incident when the alert is first created, so it can be read back here.

1. In Rootly → **Settings → Custom Fields**, create a field with key `zabbix_event_id`
2. In Zabbix, make sure the Rootly media type contains a value of `{EVENT.ID}`

**How the ID flows:**

```
1. Zabbix fires an alert
2. Zabbix media type creates a Rootly incident   ← {EVENT.ID} is embedded here
3. You acknowledge / resolve in Rootly
4. Rootly POSTs a webhook to this service
5. This service reads the event ID and calls Zabbix
```
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

```
{"event": "processing_error", "error": "Cannot close problem: trigger does not allow manual closing", ...}
```

A successful close looks like:

```
{"event": "zabbix_close", ...}
```

### HTTP 200 doesn't mean Zabbix succeeded

The service always returns 200 to Rootly immediately (before calling Zabbix) to prevent Rootly from disabling the webhook on transient failures. All Zabbix outcomes — success or error — are only visible in the service logs.

---

## Testing

## Run these where you've installed rootly2zabbix
```bash
# Send a signed test webhook (ROOTLY_WEBHOOK_SECRET must be set in .env or environment)
python3 send_test_webhook.py --event-type incident.updated.ack    --zabbix-event-id 12345
python3 send_test_webhook.py --event-type incident.updated.unack  --zabbix-event-id 12345
python3 send_test_webhook.py --event-type incident.resolved       --zabbix-event-id 12345
python3 send_test_webhook.py --event-type incident.updated.severity --zabbix-event-id 12345

# Watch logs to confirm Zabbix outcome
journalctl -u rootly2zabbix -f
```

## Check your Rootly webhook runs to see their status

1. Acknowledge or Resolve an alert in Rootly
2. Go to **Configuration → Webhooks → select the "View" icon on your webhook → Details**

---

## Event Mapping Info

| Rootly event | Condition | Zabbix action |
|---|---|---|
| `incident.updated` | `acknowledged_at` null → timestamp | Acknowledge + add comment |
| `incident.updated` | `acknowledged_at` timestamp → null | Unacknowledge + add comment |
| `incident.resolved` | event type | Close problem (if `ROOTLY_RESOLVE_CLOSES_ZABBIX=true`) |
| `incident.updated` | `severity` changed | Change severity (if `ROOTLY_SEVERITY_UPDATES_ZABBIX=true`) |
| `incident.updated` | `summary` changed | Add comment with new summary |

### Severity Mapping

| Rootly | Zabbix level | Zabbix label |
|---|---|---|
| `critical` | 5 | Disaster |
| `high` | 4 | High |
| `medium` | 3 | Average |
| `low` | 2 | Warning |
| `informational` | 1 | Information |
| (unknown) | 0 | Not classified |

Override via `ROOTLY_SEVERITY_MAP` (JSON string) in your `.env` file.

---
