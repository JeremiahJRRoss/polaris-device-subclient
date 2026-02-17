# Polaris Device Subclient — User Manual

**Version:** 0.5.0
**Last Updated:** February 2026

---

## Table of Contents

1. [What Is Polaris Device Subclient?](#1-what-is-polaris-device-subclient)
2. [Requirements](#2-requirements)
3. [Installation](#3-installation)
4. [Quick Start](#4-quick-start)
5. [How It Works](#5-how-it-works)
6. [Configuring Your Collection Agent](#6-configuring-your-collection-agent)
7. [Configuration](#7-configuration)
8. [Managing Credentials](#8-managing-credentials)
9. [Monitoring & Logs](#9-monitoring--logs)
10. [Understanding the Output](#10-understanding-the-output)
11. [Splunk Searches](#11-splunk-searches)
12. [Troubleshooting](#12-troubleshooting)
13. [Maintenance](#13-maintenance)
14. [Command Reference](#14-command-reference)
15. [Configuration Reference](#15-configuration-reference)

---

## 1. What Is Polaris Device Subclient?

Polaris Device Subclient is a small, focused application that connects to Point One Navigation's Polaris API, listens for device location state changes in real time, and writes them as structured NDJSON files (one JSON object per line) to a local directory.

It is designed to be paired with a log collection agent; such as Cribl Edge, Vector, Elastic Filebeat, or Splunk Universal Forwarder; which which will tail the output files and then forward that data to a data lake, system of analysis or aggregator. 

**What Polaris Device Subclient does:**

- Connects to the Polaris GraphQL subscription API over a secure WebSocket
- Receives device state change events as they happen
- Filters out noise (undefined and error states)
- Wraps any malformed or unrecognized messages as structured error records (never silently drops data)
- Writes clean NDJSON to rotating files for collection by your agent
- Reconnects automatically if the connection drops

**What Polaris Device Subclient does not do:**

- It does not deliver data to Splunk or Elasticsearch directly — that's your collection agent's job
- It does not manage file compression or retention — that's `logrotate`'s job
- It does not buffer or retry failed deliveries — your agent does that with years of production hardening behind it

**Why this design?** The only thing that the log collection agents can't do is manage the Polaris GraphQL websocket based subscription. Everything else — durable delivery, retry, buffering, compression, routing — is a solved problem. Polaris Device Subclient simply maintains the subscription connection and then writes push notifications to file. 
---

## 2. Requirements

**Operating System:** Linux with systemd (Ubuntu 20.04+, Debian 11+, RHEL 8+)

**Python:** 3.11 or newer

**Network:** Outbound access to the Polaris API (WSS on port 443)

**A Collection Agent:** One of the following (or equivalent):

| Agent | Notes |
|-------|-------|
| Filebeat | Tails NDJSON files, ships to Elasticsearch, Logstash, or Kafka |
| Splunk Universal Forwarder | Tails NDJSON files, ships to Splunk indexers |
| Cribl Edge | Tails NDJSON files, routes to any destination |
| Fluent Bit | Can tail files or read from stdin |
| Vector | Can tail files or read from stdin |

**Credentials:** A Polaris API key (Personal Access Token) from Point One Navigation

**Check your Python version:**

```bash
python3 --version
# Must be 3.11 or higher
```

---

## 3. Installation

```bash
cd /opt
git clone https://github.com/JeremiahJRRoss/polaris-device-subclient.git
cd /opt/polaris-device-subclient/scripts
sudo chmod +x install.sh
sudo ./install.sh
```

The installer is safe to re-run — it won't overwrite existing configuration or credentials. It creates a `polaris` service user, installs the application in a Python virtual environment at `/opt/polaris-device-subclient/`, copies configuration templates, and sets up the systemd service.

**After installation, files live in these locations:**

| Path | Purpose |
|------|---------|
| `/opt/polaris-device-subclient/` | Application code and Python venv |
| `/etc/polaris/config.json` | Main configuration file |
| `/etc/polaris/polaris-device-subclient.env` | Credentials (root-only access) |
| `/var/lib/polaris/data/` | NDJSON output files (for your agent to collect) |
| `/var/log/polaris-device-subclient/` | Application logs (when file logging is enabled) |

---

## 4. Quick Start

Three steps to get running.

### Step 1: Add Your API Key

```bash
sudo nano /etc/polaris/polaris-device-subclient.env
```

Replace `CHANGE_ME` with your Polaris API key:

```bash
POLARIS_API_KEY=pk_live_your_actual_key_here
POLARIS_API_URL=wss://graphql.pointonenav.com/subscriptions
```

Save and exit.

### Step 2: Start the Service

```bash
sudo systemctl enable --now polaris-device-subclient
```

### Step 3: Verify

```bash
# Check status
sudo systemctl status polaris-device-subclient

# Watch logs
sudo journalctl -u polaris-device-subclient -f
```

You should see a log entry with `"event": "ws_connected"`. Data is now flowing.

Check for output files:

```bash
ls -lh /var/lib/polaris/data/
```

You should see an `.ndjson.active` file that's growing.

---

## 5. How It Works

Polaris Device Subclient writes rotating NDJSON files. Your collection agent tails them independently.

```
polaris-device-subclient  ──writes──▶  /var/lib/polaris/data/*.ndjson  ◀──tails──  Your Agent
                                                                                       │
                                                                                       ▼
                                                                              Splunk / Elastic /
                                                                              S3 / Kafka / etc.
```

The two processes are fully decoupled. Either can restart independently. Files survive restarts of both processes.

A new output file starts when either the time threshold (default: 10 minutes) or the size threshold (default: 50 MB) is reached — whichever comes first. Active files have an `.ndjson.active` suffix. Completed files are renamed to `.ndjson` with an atomic `rename()` operation.

**File naming pattern:**

```
/var/lib/polaris/data/
├── events-writer01-20250215T183000Z.ndjson.active   ← being written now
├── events-writer01-20250215T170000Z.ndjson          ← completed, ready for collection
└── events-writer01-20250215T160000Z.ndjson          ← completed
```

Files ending in `.ndjson` (without `.active`) are complete and safe for your agent to collect.

---

## 6. Configuring Your Collection Agent

Point your agent at `/var/lib/polaris/data/*.ndjson` to begin collecting events.

### Splunk Universal Forwarder

Add to `inputs.conf`:

```ini
[monitor:///var/lib/polaris/data/*.ndjson]
disabled = false
sourcetype = polaris:device:statechange
index = main
```

### Filebeat

Add to `filebeat.yml`:

```yaml
filebeat.inputs:
  - type: log
    paths:
      - /var/lib/polaris/data/*.ndjson
    json.keys_under_root: true
    json.add_error_key: true
```

### Cribl Edge

Add a **File Monitor** source pointing to `/var/lib/polaris/data/*.ndjson`.

### Set Up File Cleanup

Since the subclient only writes files and never deletes them, `logrotate` manages cleanup. The installer configures this automatically. To check or adjust:

```bash
sudo cat /etc/logrotate.d/polaris-device-subclient
```

Default configuration:

```
/var/lib/polaris/data/*.ndjson {
    daily
    rotate 7
    compress
    delaycompress
    missingok
    notifempty
    copytruncate
}
```

Edit to adjust retention:

```bash
sudo nano /etc/logrotate.d/polaris-device-subclient
```

### Verify End-to-End

Check that events are reaching your destination. For Splunk:

```spl
sourcetype="polaris:device:statechange" | head 10
```

---

## 7. Configuration

The main configuration file is at `/etc/polaris/config.json`.

### 7.1 Editing

```bash
sudo nano /etc/polaris/config.json
```

After changes, restart:

```bash
sudo systemctl restart polaris-device-subclient
```

### 7.2 Validating Without Starting

```bash
/opt/polaris-device-subclient/venv/bin/polaris-device-subclient \
    --validate-config --config /etc/polaris/config.json
```

### 7.3 Settings You're Most Likely to Change

**Instance ID** — Give each instance a unique name if running multiples:

```json
{ "instance_id": "writer-01" }
```

**Device filtering** — Capture only specific devices, or exclude some:

```json
{
  "filter": {
    "drop_states": ["undefined", "error"],
    "drop_device_ids": ["noisy-device-99"],
    "keep_device_ids": []
  }
}
```

If `keep_device_ids` is empty, all devices are captured. If you add IDs, only those devices are captured.

**File rotation:**

```json
{
  "output": {
    "file": {
      "rotation": {
        "interval_seconds": 600,
        "max_size_bytes": 52428800
      }
    }
  }
}
```

A new file starts when either threshold is reached — whichever comes first.

**Application log files** — Optionally write structured logs to a rotating file in addition to stderr/journald:

```json
{
  "logging": {
    "file": {
      "enabled": true,
      "path": "/var/log/polaris-device-subclient/app.log",
      "max_size_bytes": 10485760,
      "backup_count": 5
    }
  }
}
```

When enabled, the application writes JSON-formatted log entries to the specified path. The file rotates at the size threshold and keeps up to `backup_count` previous files (e.g. `app.log`, `app.log.1`, `app.log.2`, etc.). Logs always go to stderr as well, so journald always has a copy regardless of this setting.

See [Section 15](#15-configuration-reference) for the full reference.

---

## 8. Managing Credentials

### 8.1 Environment File (Recommended)

This is the simplest approach and works well with systemd. The file is `chmod 600 root:root` — only root can read it. systemd loads it before starting the service.

```bash
sudo nano /etc/polaris/polaris-device-subclient.env
```

```bash
# Required
POLARIS_API_KEY=pk_live_your_key_here
POLARIS_API_URL=wss://graphql.pointonenav.com/subscriptions
```

After editing:

```bash
sudo systemctl restart polaris-device-subclient
```

### 8.2 Encrypted Secrets File (Optional)

For environments where you prefer not to store credentials as plain text:

```bash
# Create encrypted file and key
polaris-device-subclient secrets init \
    --output /etc/polaris/.secrets.enc \
    --key-file /etc/polaris/master.key

# Store a secret
polaris-device-subclient secrets set polaris.api_key \
    --value "pk_live_..." \
    --key-file /etc/polaris/master.key

# List stored keys (values never shown)
polaris-device-subclient secrets list --key-file /etc/polaris/master.key
```

Tell the service where to find the key file:

```bash
# In /etc/polaris/polaris-device-subclient.env
POLARIS_KEY_FILE=/etc/polaris/master.key
```

### 8.3 Security

Credentials never appear in output files, logs, or NDJSON events. All log output passes through a redaction filter that replaces secrets with `[REDACTED]`.

---

## 9. Monitoring & Logs

### 9.1 Viewing Logs

By default, operational logs go to stderr and are captured by journald.

```bash
# Live tail
sudo journalctl -u polaris-device-subclient -f

# Last hour
sudo journalctl -u polaris-device-subclient --since "1 hour ago"

# Errors only
sudo journalctl -u polaris-device-subclient -p err
```

### 9.2 Enabling Log Files

If you prefer log files on disk (in addition to journald), enable the log file feature in config:

```json
{
  "logging": {
    "file": {
      "enabled": true,
      "path": "/var/log/polaris-device-subclient/app.log",
      "max_size_bytes": 10485760,
      "backup_count": 5
    }
  }
}
```

The application manages its own log rotation when file logging is enabled. Old files are named `app.log.1`, `app.log.2`, etc., up to `backup_count`.

### 9.3 Filtering Logs with jq

Logs are structured JSON, so you can filter with `jq`:

```bash
# Connection events
sudo journalctl -u polaris-device-subclient -o cat | jq 'select(.event | startswith("ws_"))'

# Errors
sudo journalctl -u polaris-device-subclient -o cat | jq 'select(.level == "error")'

# File rotations
sudo journalctl -u polaris-device-subclient -o cat | jq 'select(.event == "file_rotated")'
```

If file logging is enabled, you can also query the log file directly:

```bash
cat /var/log/polaris-device-subclient/app.log | jq 'select(.level == "error")'
```

### 9.4 Key Log Events

| Event | Level | Meaning |
|-------|-------|---------|
| `ws_connected` | info | Connected to Polaris API |
| `ws_disconnected` | warn | Connection lost — will reconnect automatically |
| `ws_reconnecting` | info | Reconnect attempt (includes attempt number and delay) |
| `ws_error` | error | Connection error |
| `file_rotated` | info | New output file started |
| `event_dropped` | debug | Event filtered out (visible at debug level) |

### 9.5 Changing Log Level

```bash
sudo nano /etc/polaris/polaris-device-subclient.env
```

Add:

```bash
POLARIS_LOG_LEVEL=debug
```

Restart and set back to `info` when done — `debug` is verbose.

---

## 10. Understanding the Output

Every line of output is a self-contained JSON object with an `event_type` field.

### 10.1 Normal Events

When a device changes state:

```json
{
  "event_type": "state_change",
  "timestamp": "2025-02-15T18:32:01.123Z",
  "received_at": "2025-02-15T18:32:01.456Z",
  "device_id": "d290f1ee-6c54-4b01-90e6-d701748f0851",
  "device_label": "Fleet-Truck-042",
  "previous_state": "DISCONNECTED",
  "current_state": "CONNECTED",
  "latitude": 37.7749295,
  "longitude": -122.4194155,
  "altitude_m": 10.5,
  "rtk_enabled": true,
  "tags": [
    {"key": "fleet", "value": "west-coast"},
    {"key": "type", "value": "delivery"}
  ],
  "source": {
    "instance_id": "writer-01",
    "subscription_id": "a1b2c3d4-..."
  }
}
```

**Field descriptions:**

| Field | Description |
|-------|-------------|
| `event_type` | Always `"state_change"` for normal events |
| `timestamp` | Time of the position fix from Polaris (ISO 8601) |
| `received_at` | Time the subclient received and processed the event |
| `device_id` | Unique device identifier from Polaris |
| `device_label` | Human-readable device name |
| `previous_state` | RTK connection status before this event (`null` on first sight) |
| `current_state` | Current RTK connection status (e.g. `CONNECTED`, `DISCONNECTED`) |
| `latitude` / `longitude` | Device position in decimal degrees |
| `altitude_m` | Altitude in meters |
| `rtk_enabled` | Whether RTK corrections are enabled for this device |
| `tags` | Key-value metadata pairs assigned to the device |
| `source.instance_id` | Which subclient instance produced this record |
| `source.subscription_id` | The GraphQL subscription session ID |

### 10.2 Malformed Events

When the API sends something the application can't parse:

```json
{
  "event_type": "malformed",
  "timestamp": "2025-02-15T18:32:01.456Z",
  "received_at": "2025-02-15T18:32:01.456Z",
  "error": {
    "code": "parse_error",
    "message": "Unexpected token at position 42",
    "raw_payload": "{broken json...",
    "raw_payload_truncated": false
  },
  "source": {
    "instance_id": "writer-01",
    "subscription_id": "a1b2c3d4-..."
  }
}
```

These are never silently dropped. They appear in your output alongside normal events so you can monitor data quality.

**Error codes:**

| Code | Meaning |
|------|---------|
| `parse_error` | Polaris sent invalid JSON |
| `schema_mismatch` | Valid JSON but unexpected structure |
| `missing_fields` | Expected fields (e.g. device `id`) absent |
| `unknown_state` | Unrecognized connection status value |

### 10.3 File Output Structure

```
/var/lib/polaris/data/
├── events-writer01-20250215T183000Z.ndjson.active   ← being written now
├── events-writer01-20250215T170000Z.ndjson          ← completed
└── events-writer01-20250215T160000Z.ndjson          ← completed
```

Files with `.active` suffix are being written. Don't move or delete them. Files ending in `.ndjson` (no `.active`) are complete and ready for your collection agent.

---

## 11. Splunk Searches

Once events reach Splunk, some useful searches:

```spl
# All device state changes
sourcetype="polaris:device:statechange" event_type="state_change"

# Latest state per device
sourcetype="polaris:device:statechange" event_type="state_change"
| stats latest(current_state) as state latest(timestamp) as last_seen by device_id

# Devices that connected in the last hour
sourcetype="polaris:device:statechange" event_type="state_change" current_state="CONNECTED"
| where _time > relative_time(now(), "-1h")
| table timestamp device_id previous_state latitude longitude

# State transitions
sourcetype="polaris:device:statechange" event_type="state_change"
| where previous_state!=current_state
| table timestamp device_id device_label previous_state current_state

# Data quality — malformed events
sourcetype="polaris:device:statechange" event_type="malformed"
| stats count by error.code
```

---

## 12. Troubleshooting

### Service won't start

```bash
sudo journalctl -u polaris-device-subclient -n 50 --no-pager
```

| Error | Cause | Fix |
|-------|-------|-----|
| "missing required credential: polaris.api_key" | API key not set | Edit `/etc/polaris/polaris-device-subclient.env` |
| "config validation failed" | Bad config | Run `polaris-device-subclient --validate-config` |
| "Permission denied" | Directory ownership | `sudo chown -R polaris:polaris /var/lib/polaris/data` |
| "start-limit-hit" | Too many crashes | Fix the root cause, then `sudo systemctl reset-failed polaris-device-subclient && sudo systemctl start polaris-device-subclient` |

### Connected but no events

Check connection status:

```bash
sudo journalctl -u polaris-device-subclient -o cat \
    | jq 'select(.event | startswith("ws_"))' | tail -10
```

If you see repeated `ws_reconnecting` entries, verify your API key is valid and the Polaris endpoint is reachable:

```bash
curl -v https://graphql.pointonenav.com 2>&1 | head -20
```

### Events in files but not reaching your destination

This is a collection agent issue, not a subclient issue. Check your agent's logs. The most common mistake is a glob pattern that doesn't match the file path — make sure it includes `/var/lib/polaris/data/*.ndjson`.

### Disk filling up

Check if `logrotate` is configured:

```bash
cat /etc/logrotate.d/polaris-device-subclient
```

If it's missing, install it:

```bash
sudo cp /opt/polaris-device-subclient/systemd/polaris-device-subclient.logrotate \
    /etc/logrotate.d/polaris-device-subclient
```

For immediate relief:

```bash
# See what's using space
du -sh /var/lib/polaris/data/

# Compress old files
find /var/lib/polaris/data/ -name "*.ndjson" -not -name "*.active" -mtime +1 -exec gzip {} \;

# Delete very old files
find /var/lib/polaris/data/ -name "*.ndjson.gz" -mtime +7 -delete
```

### Lots of malformed events

```bash
# Check malformed event codes in recent files
grep '"event_type":"malformed"' /var/lib/polaris/data/*.ndjson 2>/dev/null \
    | jq -r .error.code | sort | uniq -c
```

| Code | Meaning | Action |
|------|---------|--------|
| `parse_error` | Polaris sent invalid JSON | Usually transient. If persistent, contact Point One support. |
| `schema_mismatch` | Valid JSON but unexpected structure | Polaris API may have changed. Check for application updates. |
| `missing_fields` | Expected fields absent | Same as above. |
| `unknown_state` | Unrecognized state value | API added new states. Update the application. |

### Application log file not being written

If you've enabled `logging.file.enabled` but no file appears:

1. Check the log path directory exists and is writable: `ls -la /var/log/polaris-device-subclient/`
2. Check ownership: `sudo chown polaris:polaris /var/log/polaris-device-subclient`
3. Verify the systemd unit allows writes: look for `ReadWritePaths` in the service file
4. Check journald for startup errors: `sudo journalctl -u polaris-device-subclient -n 20`

---

## 13. Maintenance

### Updating

```bash
cd /opt/polaris-device-subclient
git pull
sudo ./scripts/install.sh
sudo systemctl restart polaris-device-subclient
```

The installer preserves existing configuration and credentials.

### Changing Credentials

```bash
sudo nano /etc/polaris/polaris-device-subclient.env
sudo systemctl restart polaris-device-subclient
```

### Changing Configuration

```bash
sudo nano /etc/polaris/config.json
sudo systemctl restart polaris-device-subclient
```

### Rotating Encrypted Secrets Key

```bash
polaris-device-subclient secrets rekey \
    --key-file /etc/polaris/master.key \
    --new-key-file /etc/polaris/master-new.key
sudo mv /etc/polaris/master-new.key /etc/polaris/master.key
sudo chmod 600 /etc/polaris/master.key
sudo systemctl restart polaris-device-subclient
```

### Uninstalling

```bash
sudo systemctl stop polaris-device-subclient
sudo systemctl disable polaris-device-subclient
sudo rm /etc/systemd/system/polaris-device-subclient.service
sudo rm /etc/systemd/system/polaris-device-subclient-file.service
sudo systemctl daemon-reload
sudo rm /etc/logrotate.d/polaris-device-subclient 2>/dev/null
sudo userdel polaris
sudo rm -rf /opt/polaris-device-subclient /etc/polaris /var/lib/polaris /var/log/polaris-device-subclient
```

---

## 14. Command Reference

### Running

```
polaris-device-subclient [OPTIONS]

  -o, --output MODE          stdout or file (default: file)
  -d, --output-dir PATH      Override output directory
  -c, --config PATH          Config file path (default: /etc/polaris/config.json)
      --log-level LEVEL       debug, info, warn, error
      --dry-run               Connect, receive a few events, output, exit
      --validate-config       Check config and credentials, then exit
      --polaris-api-key KEY   Override API key
      --polaris-api-url URL   Override API URL
      --version               Show version and exit
```

The `--output stdout` mode is available for debugging and dry-run testing. In production, use the default file mode.

### Encrypted Secrets

```
polaris-device-subclient secrets init    --output PATH --key-file PATH
polaris-device-subclient secrets set     KEY --value VALUE --key-file PATH
polaris-device-subclient secrets list    --key-file PATH
polaris-device-subclient secrets rekey   --key-file PATH --new-key-file PATH
```

### Environment Variables

| Variable | Purpose |
|----------|---------|
| `POLARIS_API_KEY` | Polaris API key (required) |
| `POLARIS_API_URL` | Polaris API endpoint (required) |
| `POLARIS_OUTPUT` | Output mode: `stdout` or `file` |
| `POLARIS_OUTPUT_DIR` | File output directory |
| `POLARIS_CONFIG` | Config file path |
| `POLARIS_LOG_LEVEL` | Log verbosity |
| `POLARIS_KEY_FILE` | Encrypted secrets key file path |

---

## 15. Configuration Reference

Complete config file with all options:

```json
{
  "instance_id": "writer-01",

  "polaris": {
    "api_url": "${POLARIS_API_URL:-wss://graphql.pointonenav.com/subscriptions}",
    "api_key": "${POLARIS_API_KEY}",
    "subscription": "devices",
    "reconnect": {
      "initial_delay_ms": 1000,
      "max_delay_ms": 60000,
      "backoff_multiplier": 2,
      "jitter_pct": 20
    }
  },

  "filter": {
    "drop_states": ["undefined", "error"],
    "drop_device_ids": [],
    "keep_device_ids": []
  },

  "output": {
    "file": {
      "output_dir": "/var/lib/polaris/data",
      "file_prefix": "events",
      "rotation": {
        "interval_seconds": 600,
        "max_size_bytes": 52428800
      },
      "flush": {
        "interval_ms": 1000,
        "every_n_events": 50
      }
    }
  },

  "logging": {
    "level": "info",
    "format": "json",
    "output": "stderr",
    "file": {
      "enabled": false,
      "path": "/var/log/polaris-device-subclient/app.log",
      "max_size_bytes": 10485760,
      "backup_count": 5
    },
    "redact_patterns": ["*key*", "*token*", "*secret*", "*password*"]
  }
}
```

### Settings

**polaris.reconnect:**

| Setting | Default | Description |
|---------|---------|-------------|
| `initial_delay_ms` | 1000 | First reconnect delay |
| `max_delay_ms` | 60000 | Maximum reconnect delay |
| `backoff_multiplier` | 2 | Delay multiplier per attempt |
| `jitter_pct` | 20 | Random jitter to prevent thundering herd |

**filter:**

| Setting | Default | Description |
|---------|---------|-------------|
| `drop_states` | ["undefined", "error"] | RTK connection status values to discard |
| `drop_device_ids` | [] | Device IDs to always ignore |
| `keep_device_ids` | [] | If non-empty, only capture these devices |

**output.file:**

| Setting | Default | Description |
|---------|---------|-------------|
| `output_dir` | /var/lib/polaris/data | Where to write NDJSON files |
| `file_prefix` | events | Filename prefix |
| `rotation.interval_seconds` | 600 | Rotate after 10 minutes |
| `rotation.max_size_bytes` | 52428800 | Rotate at 50 MB |
| `flush.interval_ms` | 1000 | Flush writes every 1 second |
| `flush.every_n_events` | 50 | Flush after 50 buffered events |

**logging:**

| Setting | Default | Description |
|---------|---------|-------------|
| `level` | info | Log verbosity: debug, info, warn, error |
| `format` | json | Log format |
| `output` | stderr | Log destination (always stderr for systemd/journald) |
| `redact_patterns` | ["\*key\*", "\*token\*", ...] | Patterns for secret redaction |

**logging.file:**

| Setting | Default | Description |
|---------|---------|-------------|
| `enabled` | false | Write application logs to a rotating file |
| `path` | /var/log/polaris-device-subclient/app.log | Log file path |
| `max_size_bytes` | 10485760 | Rotate when log file reaches 10 MB |
| `backup_count` | 5 | Number of rotated log files to keep |

When `logging.file.enabled` is `true`, the application writes JSON-structured logs to both stderr and the specified file. The file is automatically rotated when it reaches `max_size_bytes`. Old files are named `app.log.1` through `app.log.{backup_count}`, with `.1` being the most recent.

### Variable Syntax

| Syntax | Behavior |
|--------|----------|
| `${VAR}` | Required — service won't start if missing |
| `${VAR:-default}` | Optional — uses default if not found |
