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

Make sure you have whitelisted the Rootly IP's (TCP Port 5000) in your inbound firewall rules for where your Zabbix Server is hosted

https://docs.rootly.com/integrations/ip-whitelist

### 6. Setting up Apache Reverse Proxy to allow HTTPS requests (skip this step if not using HTTPS)

1: Enable Apache proxy modules (if not already enabled):
  `sudo a2enmod proxy proxy_http`
  `sudo systemctl restart apache2`

2: Find your Apache vhost config (i.e. `apache2-le-ssl.conf` if you used "Let's Encrypt" for signing your HTTPS certificate):
  `ls /etc/apache2/sites-enabled/`

  Edit the file and add the below within your `<Virtual Host *:443> </VirtualHost>` block

  
      # Reverse Proxy to handle HTTPS requests from Rootly
      ProxyPass /webhook http://127.0.0.1:5000/webhook
      ProxyPassReverse /webhook http://127.0.0.1:5000/webhook
      ProxyPass /acknowledge http://127.0.0.1:5000/acknowledge
      ProxyPassReverse /acknowledge http://127.0.0.1:5000/acknowledge
      ProxyPass /resolve http://127.0.0.1:5000/resolve
      ProxyPassReverse /resolve http://127.0.0.1:5000/resolve
      ProxyPass /health http://127.0.0.1:5000/health
      ProxyPassReverse /health http://127.0.0.1:5000/health

3: Verify configuration is correct
   
  ```bash
  sudo a2enmod proxy proxy_http
  # configtest should say Syntax OK before you reload
  sudo apache2ctl configtest
  sudo systemctl reload apache2
  ```



### 7. Verify

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
This allows the resolution of alerts, if you don't add this, it will only acknowledge alerts.

1. Go to **Data Collection → Templates**
2. Select your template → **Triggers** → open the trigger
3. Check **Allow manual close** → **Update**
   - You can also multi-select triggers and use **Mass Update**

---

## Rootly Setup

There are two different ways to set this up depending on how you handle alerts. Either you directly get alerts through Routes **(Option A)** or you use Workflows **(Option B)** to create an Incident first and then an alert that pages users. This will cover both.

(THIS MIGHT NOT BE NEEDED ANYMORE, ONLY ADD ROOTLY WEBHOOK SECRET) For both options, you wll need to add your Zabbix API key in Rootly to **Configuration** > **Secrets** > **+ Create Secret**: 

   - Name: `zabbix_api_token`
   - Kind: `Built-in`
   - Secret: Enter your Zabbix API Key

### Option A: Configure workflow for Alerts that come through Routes 

1. In Rootly, go to **Configuration → Workflows**
2. Click **Create Workflow** and select **Alert** workflow type
3. Set these variables:
      - **Name**: "Auto Acknowledge Original Alert in Zabbix"
      - **Description**: "Acknowledges the original (non-workflow) alert in Zabbix once the identical Alert received in Rootly is acknowledged"
      - **Triggers**: `Alert Status Updated`
      - **Conditions**: Run this workflow if **all of** the following conditions are true (then add the following conditions)
         - **Payload**: `$.original_source` **is** `Zabbix`
         - **Status**: **is** `acknowledged`
      - **Actions**: Add action "HTTP Client"
         - **Url**:  `https://your-zabbix-url/api_jsonrpc.php`
         - **Method**: `Post`
         - **Body Parameters**:
```json
    {
     "jsonrpc": "2.0",
     "method": "event.acknowledge",
     "params": {
       "eventids": ["{{ alert.id }}"],
       "action": 6,
       "message": "Acknowledged in Rootly ({{ alert.short_id }}) by {{ alert.responders[0].name }}"
     },
     "auth": "{{ secrets.zabbix_api_token }}",
     "id": 1
   }
```

### Option B: Configure workflow for Alerts that come through other Workflows

1. Create a Custom Field for Zabbix Event ID in **Configuration > Fields > Custom Fields**, create a field with key `zabbix_event_id` with **Field Type** "Text"
    - Once you've created it, click on it and it will show you the **ID** for this (i.e. e5b462b3-c2e1-44c6-a592-52sdfs6c42dba4). *Copy this as you will need it for the Custom Fields Mapping*
2. In Rootly, edit the Workflow (**Configuration → Workflows**) that creates the Incident when you receive an alert from Zabbix
      - Add the below to your `Create Incident` action under `Custom Fields Mapping` and replace `your_custom_field_ID` with your Custom Field ID from Step 1
```json
{
   "form_field_selections_attributes":[
    {
      "form_field_id":"your_custom_field_ID",
      "value": "{{ alert.data.alert_id }}"
    }]
}
```
3. 


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
