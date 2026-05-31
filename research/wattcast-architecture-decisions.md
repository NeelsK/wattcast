# WattCast — Architecture Decisions & Rationale

**Last updated:** 2026-05-27

---

## Context

WattCast.co.za is a platform that combines solar, weather, and loadshedding data to intelligently
optimize solar system settings and smart switch control. The target market is SA residential solar
customers, with a partnership model via a local solar installer.

---

## Partnership Model

- **Installer:** Neels' own solar installer, very interested in partnership
- **Installer's current stack:** SunSynk app for monitoring — no SA, no custom automation
- **Primary inverter brand:** Deye/SunSynk, but also GoodWe, Growatt, Victron, Axpert and others
- **Distribution model:** WattCast bundled into installer's offering — included in install package
- **Key insight:** If installer standardizes on SA + WattCast Pi for all installs, the SA dependency
  becomes a feature not a constraint — every customer gets the same clean integration

---

## Decision 1: Use Solar Assistant as the Inverter Integration Layer

**Decision:** WattCast talks to Solar Assistant, not directly to inverters.

**Rationale:**
- SA supports every inverter brand the installer works with (Deye, GoodWe, Growatt, Victron,
  Axpert, Solis, Sungrow, and 10+ more)
- Rolling our own RS485/Modbus/CAN protocol layer per brand = months of work per brand
- SA team has spent years on this — it's their core competency
- SA's MQTT API is clean, well-documented, and brand-agnostic
- Installer can standardize on SA for all installs going forward

**Trade-off accepted:** Customers need SA installed. Cost ~R1,200/yr or one-time (to confirm).
This is negligible in the context of a solar install and adds real monitoring value independently.

---

## Decision 2: WattCast Runs on a Separate Pi, Not the SA Pi

**Decision:** WattCast runs on its own dedicated Pi per site.

**Rationale:**
- SA updates via versioned binary swap (`influx-bridge.v<date>`) — could break co-installed software
- SA Pi is someone else's managed device — no stability guarantees for additions
- Separate Pi = WattCast owns its own update cycle and hardware lifecycle
- If SA Pi is replaced, WattCast Pi survives independently
- Scales cleanly — installer includes pre-configured WattCast Pi in install package
- Remote management of WattCast Pis doesn't touch SA

**Hardware:** Pi Zero 2W (~R300-400) is sufficient for the automation logic. Pi 3/4 if more
headroom needed. Pre-configured SD card image = zero customer setup required.

---

## Decision 3: MQTT as the Primary Integration Protocol

**Decision:** WattCast subscribes to SA's MQTT broker for live data and publishes to `set/` topics
for inverter control.

**Rationale:**
- MQTT is open on port 1883, no auth required, accessible from LAN
- Works fully offline — no internet needed for the control loop
- Push-based — no polling, SA pushes updates as they happen
- Same API regardless of inverter brand (SA abstracts the hardware)
- Clean separation: SA owns inverter comms, WattCast owns intelligence

**MQTT broker:** Mosquitto on SA Pi, `allow_anonymous true`, must be enabled in SA UI.

**Topic pattern:**
- Read: `solar_assistant/total/<measurement>/state` and `solar_assistant/inverter_1/<measurement>/state`
- Write: `solar_assistant/inverter_1/set/<setting>`

---

## Decision 4: Internet Is Optional, Not Required for Core Function

**Decision:** WattCast must operate fully offline for its core automation loop.

**Rationale:**
- SA in South Africa: loadshedding often correlates with infrastructure stress and connectivity issues
- Worst case without this: loadshedding starts, internet drops, WattCast can't respond
- Loadshedding schedule cacheable locally (update when internet available)
- Weather forecast cacheable locally (update periodically)
- Inverter control via MQTT is LAN-only anyway

**What needs internet:**
- Loadshedding schedule sync (cache locally, works stale for days)
- Weather forecast updates (cache locally, works stale for hours)
- Remote monitoring/management dashboard (degraded gracefully when offline)
- WattCast software updates

---

## Decision 5: SunSynk Cloud API Is Not the Primary Path

**Decision:** Do not build primarily on the SunSynk cloud API (`api.sunsynk.net`).

**Rationale:**
- Cloud dependency breaks offline scenarios (see Decision 4)
- SA covers SunSynk and all other brands via same MQTT API
- Cloud API adds latency vs local MQTT

**Exception:** SunSynk cloud API could be used as a fallback or for remote dashboard access
where SA is not installed (future consideration).

---

## Deferred Decisions

| Decision | Status | Notes |
|---|---|---|
| WattCast Pi hardware spec | Deferred | Pi Zero 2W likely sufficient; confirm after load testing |
| SA licensing model for installer | Deferred | Confirm one-time vs subscription cost with SA |
| WattCast pricing model | Deferred | Discuss with installer — bundled vs subscription vs one-time |
| Multi-site remote management | Deferred | How WattCast Pis phone home for monitoring/updates |
| Smart switch integration | Deferred | Separate hardware layer TBD |
| Weather data source | Deferred | Open-Meteo (free) most likely |
| Tech stack for WattCast itself | Deferred | Python likely given pandas/data work; confirm after PoC |

---

## Next Concrete Steps

1. **Get RS485 cable splitter installed** → unlocks live inverter data and full MQTT topic stream
2. **Discuss partnership model with installer** → pricing, what he'll bundle, which brands to prioritize
3. **Build a PoC** → WattCast Pi on LAN, subscribe to SA MQTT, log data, make one automated decision
4. **Confirm SA licensing cost** → one-time or subscription, and whether installer gets bulk pricing
