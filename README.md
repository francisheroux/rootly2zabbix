# rootly2zabbix

Receives Rootly webhook events and mirrors alert state changes (acknowledge and resolved) back into Zabbix via the Zabbix API (inspired by `sonic-com`'s [pagerduty2zabbix](https://github.com/sonic-com/pagerduty2zabbix))

## How rootly2zabbix Works

  rootly2zabbix is a small Flask web service that acts as a bridge between [Rootly](https://rootly.com) (an incident management platform) and [Zabbix](https://www.zabbix.com/) (open source monitoring platform). 
  
  When something happens to an alert in Rootly, this bridge reflects that change back into the corresponding Zabbix event (acknowledge/resolved).

### The Core Flow

  1. Rootly sends a webhook to this service whenever an alert is acknowledged or resolved.
  2. The service verifies the request is genuinely from Rootly using HMAC-SHA256 signature verification (replay attack protection included — rejects requests older than 5 minutes).
  3. The Zabbix event ID is extracted from the Rootly alert payload.
  4. The event is routed to the appropriate Zabbix action based on event type.
  5. Zabbix is updated via its JSON-RPC API (event.acknowledge).

#### Direct Endpoints (for Rootly Workflows)

  Because Rootly doesn't have built-in webhooks endpoints for alerts being acknowleged or resolved, two endpoints were created so that Rootly      
  Workflows can call directly:

  - POST /acknowledge: Acknowledges a Zabbix event by ID
  - POST /resolve: Closes a Zabbix event by ID 

#### Infrastructure

  - Runs as a systemd service using gunicorn
  - All webhook processing happens in a background thread so the service immediately returns 200 OK to Rootly (preventing Rootly from disabling the webhook on transient Zabbix   
  errors)

## Requirements

  - Rootly Media Type in Zabbix (I have created one here: [https://github.com/zabbix/zabbix/pull/166](https://github.com/francisheroux/zabbix/tree/master) which is currently a PR. If you have your own, make sure it includes key `eventid` with value `{EVENT.ID}` so Rootly can match the Zabbix Event ID of the alert)
    
  - Zabbix Alert Source in Rootly (the README.md in the Rootly Media Type has instructions for this if you don't have one setup already)

---

## Zabbix Setup

### Create API token

1. Go to **Users > API tokens**
2. Click **Create API token**
3. Assign the token to a user (normally the same user as your Rootly Media Type.) with read-write access to all groups. 
4. Copy the generated token (this is your `ZABBIX_TOKEN` for your `.env` file in rootly2zabbix later on)

### Note about alert closure
If rootly2zabbix cannot close/resolve an alert, it will suppress the alert for 3 days with a note. The duration value can be customized in your `.env` file under `ZABBIX_SUPPRESS_DURATION_DAYS` and if you don't want it to be suppressed you can change `ZABBIX_SUPPRESS_ON_CLOSE_FAILURE` to `false`.

An example of this is that discovered triggers can only be closed with "Allow Manual Close" enabled on the trigger. The issue is that when this is enabled, if the underlying problem is not solved, it will close but will 
immediately trigger again. The workaround for these kinds of scenario is to suppress the alert for the alotted time you think it will take for the underlying issue to actually be resolved. 

---

## Rootly Setup

There are two different ways to set this up depending on how you handle alerts. Either you directly get alerts through Routes **(Option A)** or you use Workflows **(Option B)** to create an Incident first and then an alert that pages users. This will cover both.

  - Create a Custom Field for Zabbix alert Event ID in **Alerts > Alert Sources > Zabbix > Fields > + Add Field**, create a field with key `zabbix_event_id` with `{{ alert.data.alert_id }}` as the value (click on Zabbix alert to make sure this fills in the Alert ID properly in the preview). This is how Rootly properly identifies the right alert to ack/resolve in Zabbix

### Option A: Configure workflow for Alerts that come through Routes 

#### Acknowledge Original Alert Workflow from Routes

1. In Rootly, go to **Configuration > Workflows**
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
         - **Secret**: Copy this. This will go into your `.env` file as the `ROOTLY_WEBHOOK_SECRET` in rootly2zabbix later
         - **Body Parameters**:
```json
  {
    "zabbix_event_id": "{{ alert.data.alert_id }}",
    "message": "Acknowledged in Rootly ({{ alert.short_id }}) by {{ alert.responders | first | get:"name" }}"
  }
```

#### Resolve Original Alert Workflow from Routes

1. In Rootly, go to **Configuration > Workflows**
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
    "message": "Resolved in Rootly ({{ alert.short_id }}) by {{ alert.responders | first | get:"name" }}"
  }
```

### Option B: Configure workflow for Alerts that come through other Workflows

#### Before adding new Workflows, perform the following to make sure the Zabbix Event ID for the alert is attached to the Incident

1. Create a Custom Form Field for Zabbix Event ID in **Configuration > Fields > Custom Fields**, create a field with key `zabbix_event_id` with **Field Type** "Text"
   - Once you've created it, click on it and it will show you its **ID** (i.e. e5b46-4c6-a592-52s...) *Copy this as you will need it for the Custom Fields Mapping*
2. Get this Custom Field ID by **Configuration > Alert Fields >  click on it and it will show you the **ID** for this (i.e. e5b462b3-c2e1-44c6-a592-52sdfs6c42dba4). *Copy this as you will need it for the Custom Fields Mapping* 
3. In Rootly, edit the Workflow (**Configuration > Workflows**) that creates the Incident when you receive an alert from Zabbix
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

1. In Rootly, go to **Configuration > Workflows**
2. Click **Create Workflow** and select **Alert** workflow type
3. Set these variables:
      - **Name**: "Auto Acknowledge Workflow Alert in Zabbix"
      - **Description**: "Acknowledges the alert from a workflow in Zabbix once the identical Alert received in Rootly is acknowledged"
      - **Triggers**: `Alert Status Updated`
      - **Conditions**: Run this workflow if **all of** the following conditions are true (then add the following conditions)
         - **Source**: **is one of** `workflow`
         - **Payload**: `$.workflow.id` **is** (enter the Workflow ID of the Workflow that pages the user. Put it inbetween forward slashses like "`/a246-4c6-a592-4343sd2343erew/`" and check **Use regexp**)
         - **Status**: **is** `acknowledged`
      - **Actions**: Add action "HTTP Client"
         - **Url**:  `https://your-zabbix-url/acknowledge` or if using HTTP `http://your-zabbix-url:5000/acknowledge`
         - **Method**: `Post`
         - **Succeed On Status**: `202`
         - **Header Parameters**: `{"X-API-Key": "{{ secrets.rootly_webhook_secret }}","Content-Type": "application/json"}`
         - **Secret**: Copy this. This will go into your `.env` file as the `ROOTLY_WEBHOOK_SECRET`
         - **Body Parameters**:
```json
 {%- assign zabbix_id = nil -%}
  {%- for cfs in alert.incidents.first.custom_field_selections -%}
    {%- if cfs.custom_field.slug == "zabbix_event_id" -%}
      {%- assign zabbix_id = cfs.selected_options.value -%}
    {%- endif -%}
  {%- endfor -%}
  {
    "zabbix_event_id": "{{ zabbix_id }}",
    "message": "Acknowledged in Rootly (#{{ alert.short_id }}) by {{ alert.responders | first | get:"name" }}"
  }
```

#### Resolve Alert Workflow created from another Workflow

1. In Rootly, go to **Configuration > Workflows**
2. Click **Create Workflow** and select **Alert** workflow type
3. Set these variables:
      - **Name**: "Auto Resolve Workflow Alert in Zabbix"
      - **Description**: "Resolves the alert from a workflow in Zabbix once the identical Alert received in Rootly is resolved"
      - **Triggers**: `Alert Status Updated`
      - **Conditions**: Run this workflow if **all of** the following conditions are true (then add the following conditions)
         - **Source**: **is one of** `workflow`
         - **Payload**: `$.workflow.id` **is** (enter the Workflow ID of the Workflow that pages the user. Put it inbetween forward slashses like "`/a246-4c6-a592-4343sd2343erew/`" and check **Use regexp**)
         - **Status**: **is** `resolved`
      - **Actions**: Add action "HTTP Client"
         - **Url**:  `https://your-zabbix-url/resolve` or if using HTTP `http://your-zabbix-url:5000/resolve`
         - **Method**: `Post`
         - **Succeed On Status**: `202`
         - **Header Parameters**: `{"X-API-Key": "{{ secrets.rootly_webhook_secret }}","Content-Type": "application/json"}`
         - **Body Parameters**:
```json
 {%- assign zabbix_id = nil -%}
  {%- for cfs in alert.incidents.first.custom_field_selections -%}
    {%- if cfs.custom_field.slug == "zabbix_event_id" -%}
      {%- assign zabbix_id = cfs.selected_options.value -%}
    {%- endif -%}
  {%- endfor -%}
  {
    "zabbix_event_id": "{{ zabbix_id }}",
    "message": "Resolved in Rootly (#{{ alert.short_id }}) by {{ alert.responders | first | get:"name" }}"
  }
```
---

## rootly2zabbix Setup

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
# > {"status": "ok"}

# Live logs
journalctl -u rootly2zabbix -f
```
---

## Testing

#### Check your Rootly webhook runs to see their status

1. Acknowledge or Resolve an alert in Rootly
2. Go to **Workflows > Your workflow settings > View Runs**
   
#### In your Zabbix Server, you can view the requests coming in succesfully while watching the rootly2zabbix service logs
```bash
# Watch logs to confirm Zabbix outcome
journalctl -u rootly2zabbix -f
```


