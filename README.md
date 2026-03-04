# rootly2zabbix

Receives Rootly webhook events and mirrors incident state changes (acknowledge, unacknowledge, severity, resolve) back into Zabbix via the Zabbix API.

**The flow**

```
1. Zabbix fires an alert
2. Zabbix media type creates a Rootly alert
3. You acknowledge / resolve in Rootly
4. Rootly POSTs a webhook to rootly2zabbix service through the Rootly Workflows
5. rootly2zabbix service reads the Zabbix alert event ID and calls Zabbix API
```
---

### Requirements

  - Rootly Media Type in Zabbix (I have created one here: https://github.com/zabbix/zabbix/pull/166 which is currently a PR. If you have your own, make sure it includes key `eventid` with value `{EVENT.ID}` so Rootly can match the Zabbix Event ID of the alert)
    
  - Zabbix Alert Source in Rootly (the README.md in the Rootly Media Type has instructions for this if you don't have one setup already)

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

Make sure you have whitelisted the Rootly IP's (TCP Port 443 for HTTPS or TCP Port 5000 for HTTP) in your inbound firewall rules for where your Zabbix Server is hosted

https://docs.rootly.com/integrations/ip-whitelist

### 6. Setting up Apache Reverse Proxy to allow HTTPS requests (skip this step if not using HTTPS)

1: Enable Apache proxy modules (if not already enabled):
  `sudo a2enmod proxy proxy_http && sudo systemctl restart apache2`

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

# Health endpoint for Gunicorn
curl http://localhost:5000/health
# or for Apache for users using HTTPS
https://your-zabbix-server/health  
# → {"status": "ok"}

# Live logs
journalctl -u rootly2zabbix -f
```

## Zabbix Setup

### Create API token

1. Go to **Users → API tokens**
2. Click **Create API token**
3. Assign the token to a user (normally the same user as your Rootly Media Type.) with read-write access to all groups. 
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

  - Create a Custom Field for Zabbix alert Event ID in **Alerts > Alert Sources > Zabbix > Fields > + Add Field**, create a field with key `zabbix_event_id` with `{{ alert.data.alert_id }}` as the value (click on Zabbix alert to make sure this fills in the Alert ID properly in the preview). This is how Rootly properly identifies the right alert to ack/resolve in Zabbix

### Option A: Configure workflow for Alerts that come through Routes 

#### Acknowledge Original Alert Workflow from Routes

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
         - **Url**:  `https://your-zabbix-url/acknowledge` or if using HTTP `http://your-zabbix-url:5000/acknowledge`
         - **Method**: `Post`
         - **Succeed On Status**: `202`
         - **Header Parameters**: `{"X-API-Key": "{{ secrets.rootly_webhook_secret }}","Content-Type": "application/json"}`
         - **Body Parameters**:
```json
  {
    "zabbix_event_id": "{{ alert.data.alert_id }}",
    "message": "Acknowledged in Rootly ({{ alert.short_id }}) by {{ PERSON WHO ACK THE ALERT (WIP) }}"
  }
```

#### Resolve Original Alert Workflow from Routes

1. In Rootly, go to **Configuration → Workflows**
2. Click **Create Workflow** and select **Alert** workflow type
3. Set these variables:
      - **Name**: "Auto Resolve Original Alert in Zabbix"
      - **Description**: "Resolves the original (non-workflow) alert in Zabbix once the identical Alert received in Rootly is marked resolved"
      - **Triggers**: `Alert Status Updated`
      - **Conditions**: Run this workflow if **all of** the following conditions are true (then add the following conditions)
         - **Payload**: `$.original_source` **is** `Zabbix`
         - **Status**: **is** `resolved`
      - **Actions**: Add action "HTTP Client"
         - **Url**:  `https://your-zabbix-url/resolve` or if using HTTP `http://your-zabbix-url:5000/resolve`
         - **Method**: `Post`
         - **Succeed On Status**: `202`
         - **Header Parameters**: `{"X-API-Key": "{{ secrets.rootly_webhook_secret }}","Content-Type": "application/json"}`
         - **Body Parameters**:
```json
  {
    "zabbix_event_id": "{{ alert.data.alert_id }}",
    "message": "Resolved in Rootly ({{ alert.short_id }}) by {{ PERSON WHO ACK THE ALERT AND RESOLVE MESSAGE (WIP) }}"
  }
```

### Option B: Configure workflow for Alerts that come through other Workflows

#### Before adding new Workflows, perform the following to make sure the Zabbix Event ID for the alert is attached to the Incident

1. Create a Custom Form Field for Zabbix Event ID in **Configuration > Fields > Custom Fields**, create a field with key `zabbix_event_id` with **Field Type** "Text"
   - Once you've created it, click on it and it will show you its **ID** (i.e. e5b462b3-c2e1-44c6-a592-52sdfs6c42dba4). *Copy this as you will need it for the Custom Fields Mapping*
2. Get this Custom Field ID by **Configuration > Alert Fields >  click on it and it will show you the **ID** for this (i.e. e5b462b3-c2e1-44c6-a592-52sdfs6c42dba4). *Copy this as you will need it for the Custom Fields Mapping* 
3. In Rootly, edit the Workflow (**Configuration → Workflows**) that creates the Incident when you receive an alert from Zabbix
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
#### Acknowledge Alert Workflow created from another Workflow

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
         - **Url**:  `https://your-zabbix-url/acknowledge` or if using HTTP `http://your-zabbix-url:5000/acknowledge`
         - **Method**: `Post`
         - **Succeed On Status**: `202`
         - **Header Parameters**: `{"X-API-Key": "{{ secrets.rootly_webhook_secret }}","Content-Type": "application/json"}`
         - **Body Parameters**:
```json
  {
    "zabbix_event_id": "{{ alert.data.alert_id }}",
    "message": "Acknowledged in Rootly ({{ alert.short_id }}) by {{ PERSON WHO ACK THE ALERT (WIP) }}"
  }
```

#### Resolve Alert Workflow created from another Workflow

1. In Rootly, go to **Configuration → Workflows**
2. Click **Create Workflow** and select **Alert** workflow type
3. Set these variables:
      - **Name**: "Auto Resolve Original Alert in Zabbix"
      - **Description**: "Resolves the original (non-workflow) alert in Zabbix once the identical Alert received in Rootly is marked resolved"
      - **Triggers**: `Alert Status Updated`
      - **Conditions**: Run this workflow if **all of** the following conditions are true (then add the following conditions)
         - **Payload**: `$.original_source` **is** `Zabbix`
         - **Status**: **is** `resolved`
      - **Actions**: Add action "HTTP Client"
         - **Url**:  `https://your-zabbix-url/resolve` or if using HTTP `http://your-zabbix-url:5000/resolve`
         - **Method**: `Post`
         - **Succeed On Status**: `202`
         - **Header Parameters**: `{"X-API-Key": "{{ secrets.rootly_webhook_secret }}","Content-Type": "application/json"}`
         - **Body Parameters**:
```json
  {
    "zabbix_event_id": "{{ alert.data.alert_id }}",
    "message": "Resolved in Rootly ({{ alert.short_id }}) by {{ PERSON WHO ACK THE ALERT AND RESOLVE MESSAGE (WIP) }}"
  }
```




## Troubleshooting

### Resolve doesn't close the Zabbix problem

**Most common cause:** the trigger does not have "Allow manual close" enabled.

Fix in Zabbix UI: **Configuration → Triggers → [edit trigger] → check "Allow manual close" → Update**

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

---

## Testing

#### Check your Rootly webhook runs to see their status

1. Acknowledge or Resolve an alert in Rootly
2. Go to **Workflows → Your workflow settings → View Runs**
   
#### In your Zabbix Server, you can view the requests coming in succesfully while watching the rootly2zabbix service logs
```bash
# Watch logs to confirm Zabbix outcome
journalctl -u rootly2zabbix -f
```


