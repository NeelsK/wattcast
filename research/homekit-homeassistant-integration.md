# HomeKit & Home Assistant Integration Research
## WattCast — May 2026

---

## Executive Summary

**Home Assistant: YES — can replace the Pi for existing HA customers.**
**HomeKit: NO — fundamentally unsuitable for WattCast's use case.**

The core question is whether customers who already run Apple HomeKit or Home Assistant would still need a WattCast-provided Raspberry Pi. The answer splits cleanly along platform lines.

---

## Apple HomeKit

### Why HomeKit Doesn't Work for WattCast

HomeKit requires a **local bridge device** — a WattCast cloud service cannot present itself as a HomeKit accessory without physical hardware on the customer's network. Even if that hardware problem were solved, HomeKit has no native concept of energy management: Apple provides no Service or Characteristic types for battery charge rates, inverter parameters, or charge scheduling. You cannot set a charge schedule via HomeKit automations.

Additionally, commercial HomeKit devices require **MFi certification** — Apple's licensing program for hardware makers. Open-source HAP libraries (HAP-python, HAP-NodeJS) exist but are explicitly for prototyping only; they are not an approved path for a commercial product.

The three inverter brands WattCast targets — Sunsynk, Deye/Solarman, and Victron — have no HomeKit integration as of 2026.

**HomeKit automations** can trigger time-based or sensor-based on/off actions, but cannot perform conditional multi-step logic (e.g., "charge at 30A if solar forecast > 5kWh, else charge at 10A"). This makes HomeKit useless for load optimisation.

HomeKit adoption in South Africa is also low — it requires the Apple ecosystem and the premium pricing of HomePod or Apple TV hubs, which don't match the typical SA solar installer customer.

**Verdict: HomeKit is a dead end for WattCast. Do not invest here.**

---

## Home Assistant

### What It Is

Home Assistant (HA) is an open-source, self-hosted home automation platform. It runs on hardware the customer owns — typically a Raspberry Pi 4/5, Intel NUC, or a Docker container on a NAS. All automation logic runs locally; no cloud dependency is required (though Nabu Casa offers optional paid remote access). It is completely free.

### Integration Quality for WattCast's Target Devices

All five device categories WattCast cares about have active, working integrations:

| Device | Integration type | Write control? |
|---|---|---|
| Sunsynk | HACS custom (kellerza/sunsynk) | Yes — via local Modbus or Solar Assistant MQTT |
| Deye/Solarman | Official HA core | Yes — Solarman Business API |
| Victron GX | Official HA core | Yes — local MQTT/Modbus TCP |
| Victron BLE | Official HA core | Read-only (Bluetooth sensors) |
| Victron Remote Monitoring | Official HA core | Yes — VRM cloud API |
| Shelly | Official HA core | Yes — local REST + cloud |
| Tuya | Official HA core | Yes — cloud API |

These are not workarounds — they are stable, well-maintained integrations actively used by the SA solar community.

### WattCast as a Home Assistant Custom Component

WattCast could publish an official HA integration via HACS (Home Assistant Community Store). This is a standard path — many cloud services (EcoFlow, AP Systems, Hargassner) have done exactly this.

The integration would:
1. Accept the customer's WattCast API key during setup.
2. Pull real-time data from the WattCast cloud (battery %, solar W, grid W, forecast).
3. Expose HA entities: sensors for monitoring, switches for load control, number entities for setpoints (charge current, discharge limit).
4. Expose custom HA services: `wattcast.set_charge_rate`, `wattcast.trigger_load_shed`, etc.
5. Allow WattCast's optimisation engine to push commands back down via the API.

Development timeline: approximately 3–6 months for a v1.0 custom component.

### The Critical Implication: Pi Is Optional for HA Customers

If a customer already runs Home Assistant on their own hardware, **WattCast does not need to ship them a Pi**. Their existing HA hardware orchestrates everything:

```
Customer's Home
├── Their existing Home Assistant (Pi or NUC)
│   ├── WattCast integration → WattCast cloud API (optimisation engine)
│   ├── Sunsynk integration → local Modbus
│   ├── Shelly integration → local WiFi
│   └── Automations generated from WattCast recommendations
├── Sunsynk inverter
└── Shelly relays
```

The WattCast cloud still does the heavy lifting (ML forecasting, charge scheduling, tariff optimisation) — the HA integration is just the local bridge that executes those recommendations on the customer's devices.

### Add-On vs. Custom Component

Home Assistant supports both "add-ons" (separate Docker containers) and "custom components" (Python code inside HA). For WattCast's architecture — cloud API relay with local device control — a **custom component is the right choice**. Add-ons are more appropriate when you need a heavy standalone process (e.g., a local database or ML model). Custom components work on all HA installation types; add-ons only work on Home Assistant OS.

Note: As of May 2025, HA has deprecated the "Supervised" installation method. Recommend targeting HA OS and Docker installs.

### South Africa Adoption

Home Assistant is actively used in the SA solar community. The Power Forum (South Africa's largest load-shedding and solar community forum) has threads on HA + Sunsynk, HA + Solar Assistant, and DIY automation for battery optimisation. This maps directly to WattCast's target customer. HA's local-first, no-subscription model also resonates with SA users who have unreliable internet and distrust cloud-only products.

---

## Architecture Implications

### Proposed Customer Segmentation

**Segment A — No home automation platform (majority of market today)**
WattCast ships a Raspberry Pi (current model). No change required.

**Segment B — Existing Home Assistant customer**
WattCast offers a HACS integration. Customer installs it on their existing hardware. No Pi shipped. WattCast charges a software subscription for the cloud optimisation engine. This is a lower-friction, lower-cost path that could accelerate adoption.

**Segment C — HomeKit customer**
No viable integration path. Recommend these customers install Home Assistant alongside HomeKit (many HA users bridge HA → HomeKit for voice control). Do not attempt native HomeKit integration.

### API Requirements

For the HA integration to work, WattCast's backend needs to expose:
- `GET /api/v1/plant/{id}/status` — real-time readings
- `GET /api/v1/plant/{id}/forecast` — optimisation recommendations
- `POST /api/v1/plant/{id}/commands` — execute charge/discharge/load commands
- Webhook or MQTT subscription for push updates (reduces polling load)
- Secure API key per customer (stored in HA's encrypted secrets)

If WattCast's API is already being built for the Pi-to-cloud sync, most of this likely exists or is a small extension.

---

## Recommendation

Prioritise Home Assistant integration as a **second-tier delivery mechanism** alongside the Pi. It removes the hardware cost and shipping friction for tech-savvy customers, opens a recurring software-subscription revenue model, and positions WattCast well in the growing SA HA community. HomeKit requires no further investigation.

---

*Research conducted May 2026. Sources: Home Assistant official docs, HACS registry, kellerza/sunsynk GitHub, Power Forum SA, Apple HomeKit developer documentation.*
