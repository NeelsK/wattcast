# Hardware & Wiring Guide — Sunsynk 5K-SG04LP1 → Raspberry Pi

This is the physical setup. Don't skip the safety section — RS485 itself is low voltage, but you'll be working near a live inverter.

## Shopping list

| Item | Notes | Approx ZAR |
|---|---|---|
| Raspberry Pi (Pi 3B onwards; **Pi 4 is what we're using**) | Pi 4 has plenty of headroom for Grafana / Home Assistant later | R600–R1500 |
| MicroSD card (16 GB+, A1 rated) | SanDisk / Samsung — avoid no-name cards, they fail fast under logging workloads | R150 |
| **USB-to-RS485 adapter** with the **CH340** or **FT232** chipset | Avoid CP2102 — they work but the cheap clones drop bytes. The "Waveshare USB to RS485" is reliable. | R150–R350 |
| RJ45 patch cable (Cat5/Cat6, any length) | You'll cut one end off | R30 |
| **USB-C 5V/3A (15W) power supply** for the Pi 4 | Official Raspberry Pi 15W supply preferred — under-voltage causes Modbus timeouts. **Don't use a phone charger.** | R250 |
| Heatsink or vented case | Pi 4 runs hotter than older Pis, especially in a DB enclosure. Passive heatsink is enough; an Argon ONE / FLIRC metal case is better. | R50–R600 |

Optional but useful: a small UPS HAT for the Pi (so it survives Eskom dips), and a DIN-rail enclosure if it lives in your DB board.

## The right port on the inverter

The 5K-SG04LP1 has several RJ45-style ports under the front cover, near the comms area:

```
  ┌────────────────────────────────────────┐
  │  [BMS-1] [BMS-2] [RS485] [CAN] [METER] │
  └────────────────────────────────────────┘
```

You want the port **labelled "RS485"** (sometimes silk-screened "Modbus" or "BMS-2" depending on firmware revision — check the label on the underside of the cover). **Do not** use BMS-1, that talks to your battery; hijacking it will drop battery comms and the inverter will fault.

If your inverter doesn't have a port labelled exactly "RS485", the safe pair is the **Meter** port (used by the CT meter on grid-tie installations) — but only if you don't currently have a meter installed there. If you do, you'll need a Y-splitter (and the meter takes priority on bus arbitration).

## RJ45 pinout (RS485 port)

Standard Sunsynk pinout, EIA/TIA 568B colour code:

| Pin | Colour (568B) | Signal |
|---|---|---|
| 1 | White/Orange | RS485-A (D+) |
| 2 | Orange | RS485-B (D−) |
| 3 | White/Green | GND |
| 4 | Blue | — |
| 5 | White/Blue | — |
| 6 | Green | — |
| 7 | White/Brown | — |
| 8 | Brown | — |

Some Sunsynk firmware revisions swap pins 1↔2 (A and B) — if the link doesn't work after wiring, **try swapping A and B first** before assuming anything else is wrong.

## Pi 4 — which USB port to use

The Pi 4 has two USB 3.0 ports (blue inside) and two USB 2.0 ports (black inside). **Plug the USB-RS485 adapter into a black USB 2.0 port.** The USB 3.0 controllers on the Pi 4 are known to emit RF interference that can disturb cheaper USB 2.0 serial adapters; sticking to a USB 2.0 port avoids the issue and the adapter is USB 2.0 anyway, so you lose nothing.

## Wiring the adapter

Cut one end off your patch cable, expose pins 1, 2, 3:

```
  RJ45 pin 1 (W/O)  →  A / D+   on USB-RS485 adapter
  RJ45 pin 2 (O)    →  B / D−   on USB-RS485 adapter
  RJ45 pin 3 (W/G)  →  GND      on USB-RS485 adapter (optional but recommended for long runs)
```

That's it. Plug the RJ45 into the inverter's RS485 port, plug the USB end into the Pi.

## Modbus RTU settings

These are the defaults Sunsynk ships with:

- **Baud:** 9600
- **Data bits:** 8
- **Parity:** None
- **Stop bits:** 1
- **Slave ID:** 1

If you've changed the slave ID via the inverter's LCD menu (Settings → 485 address), use that instead.

## Sanity check before any code

Before installing anything, confirm the OS sees the adapter:

```bash
ls -l /dev/ttyUSB*
# should show /dev/ttyUSB0 (or similar)

dmesg | tail
# should show "ch341-uart converter now attached to ttyUSB0" or similar
```

Then a one-shot Modbus read with `mbpoll` (a Debian package, `sudo apt install mbpoll`):

```bash
mbpoll -m rtu -b 9600 -P none -t 3 -r 184 -c 1 -a 1 /dev/ttyUSB0
# reads 1 holding register starting at address 184 (battery SOC) from slave 1
# expect a value 0–100
```

If you get a sane number back, the link is working and you can move on to the Python client.

## Safety notes

- The inverter's comms ports are SELV (low voltage), but you'll be near AC and DC bus voltages while removing the cover. **Switch off both the AC isolator and the battery breaker before opening the inverter.**
- Don't wire the adapter to the same USB hub as anything power-hungry — RS485 timing is sensitive to USB hub brownouts.
- If you're in a metal DB enclosure, bond the GND pin (RJ45 pin 3) to the enclosure earth at one end only — do **not** create a ground loop.

## Things I'm not 100% sure about (verify on your unit)

- The exact label on the RS485 port varies between Sunsynk firmware revisions. The community register map I'm coding against (kellerza/sunsynk) treats it as the dedicated Modbus port, but I haven't seen *your* unit. Check the cover label.
- Pin 1↔2 polarity: I've seen reports of swapped wiring on a small number of units. If reads time out, swap A/B first.
- If you have a CT meter installed on the Meter port, the inverter expects that bus to behave a certain way. I'd suggest leaving the Meter port alone and using the dedicated RS485 port even if it means a longer cable.
