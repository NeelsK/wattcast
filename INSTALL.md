# WattCast — Installation Guide

This guide sets up WattCast on a fresh Raspberry Pi. The goal: clone the repo, fill in your passwords, and have both services running.

## Prerequisites

- Raspberry Pi running Raspberry Pi OS Lite (64-bit, Bookworm)
- SSH access to the Pi
- Solar Assistant running on a separate Pi (note its IP)
- Ecowitt weather station on your local network
- MariaDB accessible from the Pi (for mqtt_bridge telemetry)

## 1. Create the wattcast user

```bash
sudo adduser wattcast
sudo usermod -aG sudo wattcast
```

## 2. Clone the repo

```bash
sudo apt update && sudo apt install -y git python3-pip python3-venv mosquitto
git clone https://github.com/NeelsK/wattcast.git /home/wattcast/wattcast
chown -R wattcast:wattcast /home/wattcast/wattcast
```

## 3. Set up the MQTT bridge

```bash
cd /home/wattcast/wattcast/mqtt_bridge
pip3 install -r requirements.txt --break-system-packages

# Install the service
sudo cp mqtt_bridge.service /etc/systemd/system/
sudo systemctl daemon-reload
```

Edit the installed service file to set your passwords:

```bash
sudo nano /etc/systemd/system/mqtt_bridge.service
```

Change these values:
- `MQTT_HOST` — IP of your Solar Assistant Pi
- `DB_HOST` — IP of your MariaDB server
- `DB_PASS` — your database password

Then start it:

```bash
sudo systemctl enable mqtt_bridge
sudo systemctl start mqtt_bridge
sudo journalctl -u mqtt_bridge -f   # confirm it's connecting
```

## 4. Set up the solar rule engine

```bash
cd /home/wattcast/wattcast/solar-rule-engine
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

# Copy and edit the config
cp config.yaml.example config.yaml
nano config.yaml
```

Fill in your site values in `config.yaml`:

| Section | Key | What to set |
|---|---|---|
| `location` | `latitude`, `longitude` | Your GPS coordinates |
| `location` | `timezone` | e.g. `Africa/Johannesburg` |
| `panels` | `total_kwp`, `tilt`, `azimuth` | Your panel array spec |
| `battery` | `capacity_kwh`, `min_soc` | Your battery spec |
| `mqtt` | `host` | IP of this Pi (localhost if Mosquitto is here) |
| `ecowitt` | `station_ip` or cloud keys | Your weather station |
| `switches` | `ip`, `password` | Each Shelly device |

**Leave `dry_run: true` until you've verified the logic is correct.**

Run tests first (no hardware needed):

```bash
python -m pytest tests/ -v
```

Run manually to verify:

```bash
python main.py --config config.yaml
```

Once happy, install the service:

```bash
sudo cp solar-rule-engine.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable solar-rule-engine
sudo systemctl start solar-rule-engine
sudo journalctl -u solar-rule-engine -f
```

## 5. Set up Mosquitto MQTT bridge to Solar Assistant

The solar-rule-engine reads from `localhost:1883`. Mosquitto bridges this to Solar Assistant's broker so SA topics are available locally.

Create `/etc/mosquitto/conf.d/sa-bridge.conf`:

```
connection solar-assistant
address <SA_PI_IP>:1883
topic solar_assistant/# in 0
bridge_attempt_unsubscribe false
start_type automatic
```

Restart Mosquitto:

```bash
sudo systemctl restart mosquitto
```

Verify topics are flowing:

```bash
mosquitto_sub -h localhost -t "solar_assistant/#" -v | head -20
```

## 6. Enable dry_run → live

Once you've watched the engine logs for a day and the decisions look correct:

1. Edit `config.yaml` and set `dry_run: false`
2. Restart the service: `sudo systemctl restart solar-rule-engine`
3. Watch the logs: `journalctl -u solar-rule-engine -f`

## Ongoing

| Task | Command |
|---|---|
| View bridge logs | `journalctl -u mqtt_bridge -f` |
| View engine logs | `journalctl -u solar-rule-engine -f` |
| Restart engine | `sudo systemctl restart solar-rule-engine` |
| Edit engine config | `nano /home/wattcast/wattcast/solar-rule-engine/config.yaml` |
| Update from repo | `cd /home/wattcast/wattcast && git pull` |
