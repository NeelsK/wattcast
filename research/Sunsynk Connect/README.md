# Sunsynk Connect

Local Raspberry Pi controller for the **Sunsynk 5K-SG04LP1** hybrid inverter. Monitor, log, control, and automate — over RS485, no cloud dependency.

```
   ┌─────────┐   USB   ┌─────────────┐   RS485   ┌──────────────────┐
   │   Pi    │◀──────▶│  USB-RS485  │◀────────▶│  Sunsynk         │
   │         │         │  adapter    │           │  5K-SG04LP1      │
   └─────────┘         └─────────────┘           └──────────────────┘
```

## What's in the box

| Path | What it does |
|---|---|
| `SETUP.md` | **Step-by-step Pi setup guide — start here if you're setting up a new Pi.** |
| `HARDWARE.md` | Wiring guide + shopping list. |
| `sunsynk/` | Python package — Modbus client + register map. |
| `bin/monitor.py` | Live terminal dashboard (1 Hz refresh). |
| `bin/logger.py` | Continuous SQLite logger (1 sample/min). |
| `bin/control.py` | CLI to read/write inverter settings. Dry-run by default. |
| `bin/automate.py` | YAML-driven rule engine (e.g. respond to load-shedding). |
| `automation/rules.example.yaml` | Sample rules to crib from. |
| `sql/example_queries.sql` | Useful SQL for the logger DB. |
| `systemd/` | Service units for logger + automation. |
| `udev/` | Stable `/dev/sunsynk` symlink for the adapter. |

## Quick start (on the Pi)

Assuming Raspberry Pi OS Bookworm (64-bit), as user `pi`.

```bash
# 1. Clone or copy this folder onto the Pi
cd ~
# (rsync from your laptop, or git clone, etc.)

# 2. System packages
sudo apt update
sudo apt install -y python3-venv python3-pip mbpoll sqlite3

# 3. Python virtual env
cd ~/sunsynk-connect
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# 4. Allow pi to access serial devices without sudo
sudo usermod -aG dialout pi
# (log out and back in for it to take effect)

# 5. Confirm the adapter shows up
ls /dev/ttyUSB*
# /dev/ttyUSB0

# 6. Confirm the inverter responds (sanity check before any code)
mbpoll -m rtu -b 9600 -P none -t 3 -r 184 -c 1 -a 1 /dev/ttyUSB0
# expect a value 0–100 (battery SOC %)

# 7. Run the live monitor
python3 bin/monitor.py
```

## Stable device path (recommended)

So `/dev/ttyUSB0` doesn't drift to `/dev/ttyUSB1` if you plug in another serial gadget:

```bash
lsusb     # find your adapter — note the ID xxxx:yyyy
# Edit udev/99-sunsynk-rs485.rules to match your idVendor/idProduct
sudo cp udev/99-sunsynk-rs485.rules /etc/udev/rules.d/
sudo udevadm control --reload-rules && sudo udevadm trigger
ls -l /dev/sunsynk
```

After this, every script can use `--port /dev/sunsynk` and survive reboots cleanly.

## Run logger + automation as services

```bash
# Edit the .service files if your username/path is not /home/pi/sunsynk-connect
sudo cp systemd/sunsynk-*.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now sunsynk-logger.service
sudo systemctl enable --now sunsynk-automate.service

# Check
systemctl status sunsynk-logger
journalctl -fu sunsynk-logger
```

## Reading data later

```bash
sqlite3 ~/sunsynk.db < sql/example_queries.sql
# or
sqlite3 ~/sunsynk.db "SELECT * FROM samples ORDER BY ts DESC LIMIT 5"
```

If you later want graphs without writing a UI, point Grafana at the SQLite file (the `frser-sqlite-datasource` plugin works well on a Pi).

## Writing settings safely

`bin/control.py` defaults to **dry run** — it will show what *would* happen but not actually write. Once you're confident, add `--commit`:

```bash
# Always read first
python3 bin/control.py read battery_min_soc

# Dry run (default)
python3 bin/control.py set battery_min_soc 25
# DRY RUN. Pass --commit to actually write.

# Real write — prompts for typed confirmation
python3 bin/control.py set battery_min_soc 25 --commit
```

## Things to verify on YOUR inverter

The register map I shipped is sourced from the [kellerza/sunsynk](https://github.com/kellerza/sunsynk) community project, but Sunsynk has shifted addresses between firmware revisions. Before trusting any **write** path:

1. `python3 bin/control.py read <register>` — does the value match what the inverter LCD shows?
2. If yes for all the writable registers you plan to use, you're good.
3. If a value looks wrong (e.g. battery temp at 1023°C), there's a known firmware-dependent offset on temperature registers — `(raw - 1000) / 10`. Edit `sunsynk/registers.py` accordingly.

I've flagged the registers I'm least sure about with comments in `registers.py`.

## What's not (yet) here

- **Web UI / dashboard** — easy to add later (Flask + the snapshot endpoint, or Grafana on top of SQLite).
- **MQTT publish** — useful if you later add Home Assistant. ~30 lines on top of `logger.py`.
- **Auto firmware-version detection** — currently you're trusting the register map; could probe register 87 to read firmware version and pick a map automatically.
- **EskomSePush integration** — the `loadshedding_max_discharge` rule reacts to the inverter actually losing grid voltage. To pre-emptively act on the *schedule* you'd add a small fetcher script and another rule that reads from a local file or env var.
