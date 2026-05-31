# Pi Setup Guide — From Blank SD Card to Logging Inverter

End-to-end setup for a headless Raspberry Pi running Sunsynk Connect on your LAN. Everything is done over SSH from your normal computer; the Pi never needs a monitor or keyboard.

**Target setup:** Raspberry Pi 4 (any RAM size — even 1 GB is enough) → Raspberry Pi OS Lite 64-bit (Bookworm) → Python 3.11 + the project from this folder.

**Pi 4 specifics worth knowing up-front:**
- Use a **USB-C 5V/3A (15W)** power supply. Phone chargers cause under-voltage which shows up as random Modbus timeouts and "under-voltage detected" warnings. Official RPi supply is R250 and worth it.
- Plug the USB-RS485 adapter into a **black (USB 2.0) port**, not a blue (USB 3.0) one — USB 3.0 controllers on the Pi 4 can interfere with cheap serial adapter chipsets.
- A small heatsink or vented case is recommended if it'll live in a closed DB enclosure. Pi 4 runs hotter than older Pis.

Total time: about 40 minutes, mostly waiting for things to download.

---

## Stage 1 — Flash the SD card (10 min)

Do this on your normal computer, not the Pi.

### 1.1 Install Raspberry Pi Imager

Download and install from <https://www.raspberrypi.com/software/>. It runs on Windows, macOS, and Linux.

### 1.2 Pick the OS

Open Imager and:

1. **Choose Device** → your Pi model.
2. **Choose OS** → *Raspberry Pi OS (other)* → **Raspberry Pi OS Lite (64-bit)**.
   *Lite* means no desktop — that's what you want for an always-on appliance. The full desktop version wastes RAM and SD card space on things you'll never use.
3. **Choose Storage** → your microSD card (16 GB+ recommended, 32 GB is comfortable).

### 1.3 Configure before flashing — IMPORTANT

Click the gear icon (⚙) or press `Ctrl+Shift+X`. This is what makes the headless setup possible:

- **Set hostname:** `sunsynk` *(so it's reachable as `sunsynk.local` on your LAN via mDNS)*
- **Enable SSH:** ✓ Use password authentication *(we'll switch to keys later)*
- **Set username and password:** username `pi`, set a password you'll remember
- **Configure Wi-Fi:** tick this only if your Pi will be on Wi-Fi — set SSID + password + country (`ZA` for South Africa)
- **Set locale settings:** time zone `Africa/Johannesburg`, keyboard layout whatever you like (it won't matter — you'll only use SSH)

Save settings, then click **Write**. Takes about 5 minutes.

### 1.4 Boot the Pi

1. Eject the SD card from your computer, insert into the Pi.
2. Connect the USB-RS485 adapter to a USB port (don't connect to the inverter yet — we'll do that in Stage 4).
3. If you're using Ethernet, plug it in.
4. Power on the Pi.
5. Wait 60–90 seconds for first boot.

---

## Stage 2 — First SSH connection (5 min)

### 2.1 Find the Pi on your network

From your normal computer:

```bash
ssh pi@sunsynk.local
```

If `sunsynk.local` doesn't resolve (some routers don't do mDNS), find the Pi's IP from your router's admin page — look for a device called `sunsynk` in the DHCP client list. Then:

```bash
ssh pi@192.168.1.42      # whatever IP you found
```

First connection will ask you to verify the host fingerprint — type `yes`. Enter the password you set in Imager.

You should see something like:

```
pi@sunsynk:~ $
```

### 2.2 Update the system (~5 min)

Always do this on a fresh install:

```bash
sudo apt update
sudo apt full-upgrade -y
sudo reboot
```

The Pi will drop your SSH session. Wait 30 seconds, reconnect.

### 2.3 (Optional but recommended) SSH key auth

Skip this if it's your first Linux setup and you want to keep it simple — the password works fine on a LAN-only setup. If you want to harden:

```bash
# On your normal computer (not the Pi)
ssh-keygen -t ed25519        # press enter at all prompts if you don't have a key yet
ssh-copy-id pi@sunsynk.local
```

Then test:

```bash
ssh pi@sunsynk.local        # should NOT ask for password
```

Once that works you can disable password auth:

```bash
sudo sed -i 's/^#*PasswordAuthentication.*/PasswordAuthentication no/' /etc/ssh/sshd_config
sudo systemctl restart ssh
```

---

## Stage 3 — Install required software (5 min)

All on the Pi.

### 3.1 System packages

```bash
sudo apt install -y \
    python3-venv \
    python3-pip \
    git \
    mbpoll \
    sqlite3 \
    rsync
```

Quick what's-this:
- `python3-venv` / `python3-pip` — Python virtualenv + package installer.
- `git` — for cloning repos (and easy backup).
- `mbpoll` — Modbus command-line tool, used to sanity-check the inverter link before any Python.
- `sqlite3` — CLI for poking around the logger DB.
- `rsync` — for copying this project from your laptop to the Pi.

### 3.2 Add `pi` to the `dialout` group

Without this, you'd need `sudo` every time you talk to the USB-RS485 adapter:

```bash
sudo usermod -aG dialout pi
exit          # log out
```

Then SSH back in (the group membership only takes effect on a new login):

```bash
ssh pi@sunsynk.local
groups        # should now include 'dialout'
```

---

## Stage 4 — Wire up the RS485 link and verify (10 min)

This is where you can break things — read `HARDWARE.md` first if you haven't.

### 4.1 Wire up

1. **Switch off the AC isolator AND the battery breaker on the inverter.** Wait 30 seconds.
2. Open the inverter's comms cover, plug the RJ45 patch cable into the **RS485 port** (not BMS, not Meter — see `HARDWARE.md` for the right one).
3. Other end of the patch cable goes to the USB-RS485 adapter (pins 1, 2, 3 — wiring detail in `HARDWARE.md`).
4. USB end of the adapter goes into the Pi.
5. Power the inverter back on (battery breaker first, then AC).

### 4.2 Confirm the Pi sees the adapter

```bash
ls -l /dev/ttyUSB*
# should show: /dev/ttyUSB0
```

If nothing shows, the adapter isn't being detected — try a different USB port or cable. `dmesg | tail -20` will usually say what happened.

### 4.3 Talk to the inverter (the moment of truth)

```bash
mbpoll -m rtu -b 9600 -P none -t 3 -r 184 -c 1 -a 1 /dev/ttyUSB0
```

What this reads: register 184 (battery SOC), one register, slave ID 1, RTU mode, 9600 baud.

**Expected output:**

```
-- Polling slave 1...
[184]: 67
```

A value 0–100 means it's working.

**If you get `Connection timed out` or `Modbus error`:**
- Try swapping pin 1 and pin 2 on your wiring (A/B polarity is sometimes reversed — see `HARDWARE.md`).
- Confirm slave ID with the inverter's LCD (Settings → 485 address). Default is 1.
- Confirm baud rate (Settings → Baud rate). Default is 9600.
- Make sure you're plugged into the **RS485** port, not BMS.

Don't move on until `mbpoll` works. Everything in Stage 5 builds on this.

---

## Stage 5 — Deploy the project (5 min)

### 5.1 Copy the project to the Pi

From your normal computer (where you have the `Sunsynk Connect` folder):

```bash
rsync -av --exclude '.venv' --exclude '__pycache__' \
    "/Users/neelskriek/Documents/Claude/Projects/Sunsynk Connect/" \
    pi@sunsynk.local:/home/pi/sunsynk-connect/
```

Note the trailing slashes — they matter for rsync.

### 5.2 Set up the Python venv (on the Pi)

```bash
ssh pi@sunsynk.local
cd ~/sunsynk-connect
python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
```

Takes about 2 minutes — `pymodbus` pulls in a few dependencies.

### 5.3 First live read

Still in the venv:

```bash
python3 bin/control.py status
```

Expected: a list of all live values — battery SOC, PV power, grid voltage, etc. If you see real numbers, **the software stack is working end-to-end**.

Now try the live monitor:

```bash
python3 bin/monitor.py
# Ctrl-C to quit
```

You should see the dashboard refresh once a second.

---

## Stage 6 — Stable device path (5 min)

`/dev/ttyUSB0` will become `/dev/ttyUSB1` if you ever plug in another serial adapter. Pin it to `/dev/sunsynk` so the services don't break.

### 6.1 Find your adapter's USB IDs

```bash
lsusb
# Bus 001 Device 003: ID 1a86:7523 QinHeng Electronics CH340 serial converter
```

Note the `1a86:7523` part — that's `idVendor:idProduct`. Common chipsets:
- CH340 → `1a86:7523`
- FT232 → `0403:6001`
- CP2102 → `10c4:ea60`

### 6.2 Install the udev rule

The shipped rule already has CH340. If yours is different, edit it:

```bash
nano ~/sunsynk-connect/udev/99-sunsynk-rs485.rules
# change idVendor / idProduct to match your lsusb output
```

Then install:

```bash
sudo cp ~/sunsynk-connect/udev/99-sunsynk-rs485.rules /etc/udev/rules.d/
sudo udevadm control --reload-rules
sudo udevadm trigger
```

Unplug the adapter, plug it back in, then:

```bash
ls -l /dev/sunsynk
# lrwxrwxrwx 1 root root 7 ... /dev/sunsynk -> ttyUSB0
```

If that symlink exists, the rule worked. From here on, use `--port /dev/sunsynk` everywhere.

---

## Stage 7 — Logger and automation as services (5 min)

So they start automatically on boot and restart if they ever crash.

### 7.1 Logger

```bash
# The shipped service files assume the path /home/pi/sunsynk-connect — that
# matches what we set up in Stage 5, so you can use them as-is.

sudo cp ~/sunsynk-connect/systemd/sunsynk-logger.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now sunsynk-logger.service

# Check it's running
systemctl status sunsynk-logger
# expect "active (running)"

# Tail the live output
journalctl -fu sunsynk-logger
# Ctrl-C to stop tailing (the service keeps running)
```

After a minute, confirm samples are being written:

```bash
sqlite3 ~/sunsynk.db "SELECT datetime(ts,'unixepoch','localtime'), battery_soc, load_power FROM samples ORDER BY ts DESC LIMIT 5"
```

You should see rows appearing.

### 7.2 Automation (optional — skip if you only want monitoring)

Before enabling this, **review the rules** in `automation/rules.example.yaml`. The defaults are sensible but they will write to your inverter — make sure you understand what each rule does.

Copy the example to a real config:

```bash
cp ~/sunsynk-connect/automation/rules.example.yaml \
   ~/sunsynk-connect/automation/rules.yaml
nano ~/sunsynk-connect/automation/rules.yaml      # edit / delete what you don't want
```

Test with dry-run before going live:

```bash
cd ~/sunsynk-connect
source .venv/bin/activate
python3 bin/automate.py --port /dev/sunsynk --rules automation/rules.yaml --dry-run --verbose
# Watch a few cycles, Ctrl-C when satisfied
```

If the dry run logs match what you expect, install the service:

```bash
sudo cp ~/sunsynk-connect/systemd/sunsynk-automate.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now sunsynk-automate.service
journalctl -fu sunsynk-automate
```

---

## Stage 8 — SD card longevity (5 min, do this once)

SD cards die from constant logging if you're not careful. Two cheap protections:

### 8.1 Move temp files to RAM

Edit `/etc/fstab`:

```bash
sudo nano /etc/fstab
```

Add at the bottom:

```
tmpfs   /tmp        tmpfs   defaults,noatime,size=100m   0 0
tmpfs   /var/tmp    tmpfs   defaults,noatime,size=30m    0 0
tmpfs   /var/log    tmpfs   defaults,noatime,size=50m    0 0
```

`/var/log` in tmpfs means logs are wiped on reboot — fine for a stable appliance, less fine if you're debugging. Drop that line if you'd rather keep logs on disk.

### 8.2 Disable swap

A 4 GB Pi never swaps under our workload, and swap thrashes the SD card if it ever does:

```bash
sudo systemctl disable --now dphys-swapfile
sudo apt remove --purge -y dphys-swapfile
```

### 8.3 Reboot to apply

```bash
sudo reboot
```

After reconnecting:

```bash
free -h         # 'Swap' should be 0
mount | grep tmpfs       # should list /tmp, /var/tmp, /var/log
systemctl status sunsynk-logger   # should still be running
```

---

## What you now have

```
sunsynk@LAN:
  └── /home/pi/sunsynk-connect/        the project
  └── /home/pi/sunsynk.db              SQLite log, growing ~1 MB/week
  └── /dev/sunsynk → ttyUSB0           stable adapter symlink
  └── sunsynk-logger.service           samples every 60 s
  └── sunsynk-automate.service         (optional) rule engine, every 30 s
```

You can now SSH in any time and:

```bash
# Live dashboard
cd ~/sunsynk-connect && source .venv/bin/activate && python3 bin/monitor.py

# Read or write a setting
python3 bin/control.py read battery_min_soc
python3 bin/control.py set battery_min_soc 25 --commit

# Query history
sqlite3 ~/sunsynk.db < sql/example_queries.sql
```

If Sunsynk's website goes down, none of this stops working. That was the goal.

---

## Common things that go wrong

| Symptom | Likely cause | Fix |
|---|---|---|
| `ssh: connect: No route to host` | Pi not on network yet | Wait 90 s after first boot, check router DHCP list for IP |
| `mbpoll: Connection timed out` | RS485 wiring | Swap pins 1↔2 (A/B). Check you're on the right port. |
| `Permission denied: '/dev/ttyUSB0'` | Not in `dialout` group | `sudo usermod -aG dialout pi` then log out and back in |
| Logger service "failed to start" | Path mismatch | Confirm project is at exactly `/home/pi/sunsynk-connect/` |
| `pip install` is glacially slow | piwheels mirror down | `pip install -r requirements.txt -i https://pypi.org/simple` |
| Battery temp reads 1023 | Firmware offset | Edit `sunsynk/registers.py`, change `battery_temp` decode |

---

## Things I haven't covered (and why)

- **Backup of the SQLite DB** — once you have a few months of data you'll want a cron job that copies `sunsynk.db` somewhere safe (USB stick, NAS). Easy 2-line cron.
- **Grafana / web UI** — depends on whether you want it on the Pi itself or on another always-on box. Happy to help build one once you've got the logger filling up.
- **Notifications (Telegram, email, push)** — slot into the rule engine via the `shell` action type. Tell me your preferred channel and I can wire it up.
- **EskomSePush integration** — you'd add a small fetcher script + a rule that reads from `/var/run/eskom-status.json` (or similar). Worth doing if you want pre-emptive battery charging before scheduled load-shedding.
