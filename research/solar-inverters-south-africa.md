# Solar Inverter Systems in South Africa — Research

*Last updated: 2026-05-09 (architecture note added)*

---

## WattCast Product Context

WattCast is a **B2B2C platform** targeting solar installers and their customers in South Africa.

**User model:**
- **Installers** register on WattCast, link their customers' inverters, and configure optimisation rules.
- **Customers** have their own login to view their system dashboard and see active rules, but cannot change inverter settings directly.
- One installer manages multiple customer sites.

**Scope — what WattCast controls:**
- **Inverter settings:** Primarily battery charge scheduling (when to charge from grid, charge limits, time-of-use rules). Off-grid customers are supported — they won't have grid-charge rules but still benefit from optimisation.
- **Smart switches:** Geysers and pumps are the primary loads. WattCast will integrate with any switch that exposes a webhook or documented REST API.

**Out of scope:** Full home automation. WattCast is focused purely on solar generation optimisation, battery longevity, and load management for high-draw devices.

---

## Edge Architecture — The On-Site Raspberry Pi

Each customer installation includes a Raspberry Pi that serves as the **local edge node**. This is not just a monitoring tool — it is a critical part of the WattCast architecture.

### Roles of the Pi

| Role | Detail |
|---|---|
| **Local database** | Stores time-series data from inverter and switches (InfluxDB or SQLite); survives internet outages |
| **Graph / dashboard server** | Serves a local web dashboard (e.g. Grafana) accessible on the home network without internet |
| **Rule engine fallback** | Executes WattCast rules locally if cloud is unreachable — ensures geysers and pumps still switch on schedule |
| **Modbus/RS485 link** | Connects directly to the inverter via RS485 adapter; reads real-time data and writes settings without cloud dependency |
| **Shelly local API caller** | Calls Shelly switch REST API directly on the LAN — no cloud hop, sub-second response |
| **Cloud sync agent** | When internet is available, syncs local data up to WattCast cloud and pulls updated rules down |

### Why This Matters for SA

South Africa has frequent internet outages and load-shedding events. A cloud-only architecture would mean rules fail exactly when they are most needed (during load-shedding schedules, peak tariff windows, etc.). The Pi ensures the system keeps working regardless of connectivity.

### Suggested Pi Stack

| Component | Tool | Notes |
|---|---|---|
| Time-series DB | InfluxDB or TimescaleDB | Lightweight; runs well on Pi 4 |
| Local dashboard | Grafana | Connects to InfluxDB; served on local network |
| Rule engine | Node-RED or custom Python service | Visual rule builder or scripted; syncs rules from cloud |
| Modbus bridge | `pymodbus` or `minimalmodbus` (Python) | RS485 USB adapter ~R150 |
| Shelly control | Direct HTTP calls | Local IP, no cloud needed |
| Cloud sync | Lightweight Python/Node agent | Push telemetry, pull rule updates |

### Connectivity Model

```
[Inverter] ──RS485──► [Pi: Modbus bridge]
[Shelly switches] ──LAN──► [Pi: local rule engine]
[Pi: InfluxDB] ◄── stores ── [all sensor data]
[Pi: Grafana] ──► serves local dashboard on LAN
[Pi: sync agent] ──internet (when available)──► [WattCast Cloud]
[WattCast Cloud] ──► installer/customer portal, rule config, reporting
```

### Pi Hardware Requirements
- Raspberry Pi 4 (2GB RAM minimum; 4GB recommended)
- RS485 to USB adapter (for Modbus inverter link)
- UPS or battery backup for the Pi itself — it must survive load-shedding (can be powered from inverter's USB output)
- MicroSD 32GB+ or USB SSD for database reliability

---

## Priority Integration Plan

| Priority | Brand | Reason |
|---|---|---|
| 🥇 1 | **Sunsynk** | Market leader in SA residential; full API with settings write; installer account tier |
| 🥇 1 | **Deye** | Same parent as Sunsynk; huge installed base; Solarman API with installer permissions |
| 🥈 2 | Victron | Premium segment; most open API; strong off-grid use case |
| 🥉 3 | Growatt, Solis | Commercial/mid-market; worth adding after core brands |

---

## Overview

South Africa has one of the most active residential solar markets in the world, driven by persistent load-shedding and rising electricity tariffs. Hybrid inverters (combining solar, grid, and battery management) dominate the market. The brands below represent the most installed and actively supported systems as of 2025–2026.

---

## Top Inverter Brands & Systems

### 1. Sunsynk
- **Market position:** Widely regarded as the #1 residential hybrid inverter brand in South Africa for 2025–2026.
- **Parent company:** Sunsynk Ltd (UK-registered, manufactured in China; same parent as Deye)
- **Popular models:** Sunsynk 5kW, 8kW, 12kW Hybrid Inverters
- **Key strengths:** Excellent local distributor network, strong app/monitoring ecosystem, very configurable, good battery compatibility (lithium & lead-acid)
- **App/Portal:** Sunsynk Connect app (iOS & Android) + PowerView web portal at [api.sunsynk.net](https://api.sunsynk.net)
- **API availability:** ✅ Yes — official REST API available at `https://openapi.sunsynk.net`
  - API is what powers the Sunsynk Connect apps
  - PyPI package: [`sunsynk-api-client`](https://pypi.org/project/sunsynk-api-client/)
  - Community docs: [Demystifying the Sunsynk API (4x4community)](https://www.4x4community.co.za/forum/showthread.php/366452-Demystifying-the-Sunsynk-API)
  - openHAB binding available
  - Supports reading real-time data AND changing inverter settings remotely
- **Local support:** Strong — multiple authorised distributors (e.g., Segen Solar, RS Components)

---

### 2. Deye
- **Market position:** Best-value alternative to Sunsynk; shares hardware lineage (same parent company)
- **Popular models:** Deye SUN-5K-SG03LP1-EU, SUN-8K, SUN-12K
- **Key strengths:** Lower price than Sunsynk, very similar functionality, large community
- **App/Portal:** Solarman Smart app + Inteless portal (`pv.inteless.com`)
- **API availability:** ✅ Yes — via **Solarman API**
  - Solarman Open API v1.1.0 available; requires registration/email request to `customerservice@solarmanpv.com`
  - Official docs: [doc.solarmanpv.com](https://doc.solarmanpv.com/en/Documentation%20and%20Quick%20Guide)
  - Community: [DIY Solar Forum — Monitoring Deye via Inteless API](https://diysolarforum.com/threads/monitoring-sunsynk-deye-via-intelless-api.32512/)
  - Also supports local RS485/Modbus access without cloud
- **Home Assistant integration:** ✅ Well-supported via [Deye/Sunsynk HA add-on](https://community.home-assistant.io/t/deye-sunsynk-inverter-integration-add-on/544048)

---

### 3. Victron Energy
- **Market position:** Premium brand; dominant in off-grid, marine, and high-end residential
- **Origin:** Dutch company
- **Popular models:** MultiPlus-II, Quattro, EasySolar, SmartSolar MPPT charge controllers
- **Key strengths:** Industry-leading reliability, extremely open ecosystem, best-in-class monitoring, MQTT support, local & cloud access
- **App/Portal:** VRM (Victron Remote Management) portal at [vrm.victronenergy.com](https://vrm.victronenergy.com) + VictronConnect app
- **API availability:** ✅ Fully open and well-documented
  - Official REST API docs: [vrm-api-docs.victronenergy.com](https://vrm-api-docs.victronenergy.com/)
  - Python client: [victronenergy/vrm-api-python-client (GitHub)](https://github.com/victronenergy/vrm-api-python-client)
  - Node-RED node: [victron-vrm-api](https://flows.nodered.org/node/victron-vrm-api)
  - Home Assistant official integration: [Victron Remote Monitoring](https://www.home-assistant.io/integrations/victron_remote_monitoring/)
  - Auth: POST to `https://vrmapi.victronenergy.com/v2/auth/login` → bearer token
  - Also supports local Modbus TCP and MQTT via Venus OS (GX devices)
- **Local support:** Available through specialist distributors; premium price point

---

### 4. Growatt
- **Market position:** Strong in commercial/large residential; third-largest inverter manufacturer globally
- **Popular models:** MIN, MID, MAX series (string inverters); SPH series (hybrid)
- **Key strengths:** Enterprise reliability, cost-effective at scale, wide power range
- **App/Portal:** ShinePhone app + ShineServer web portal (`server.growatt.com`)
- **API availability:** ✅ Yes — Growatt Server Open API
  - Requires registration for API access
  - Community integrations: [Home Assistant Growatt integration](https://community.home-assistant.io/t/anyone-experience-with-connecting-a-growatt-solar-inverter/60430)
  - Also supports local Modbus RS485
- **Note:** More common in commercial installations in SA than residential

---

### 5. SolarEdge
- **Market position:** Commercial and premium residential; strong globally
- **Popular models:** SE series string inverters with power optimisers
- **Key strengths:** Module-level monitoring, safety (rapid shutdown), strong warranty
- **App/Portal:** mySolarEdge app (iOS & Android) + monitoring portal at [monitoring.solaredge.com](https://monitoring.solaredge.com)
- **API availability:** ✅ Official REST API
  - Docs (PDF): [SE Monitoring API](https://knowledge-center.solaredge.com/sites/kc/files/se_monitoring_api.pdf)
  - Base URL: `https://monitoringapi.solaredge.com`
  - Auth: API key (site-level or account-level) generated in the monitoring portal
  - Primarily read-only (monitoring data); settings changes done via installer portal
  - South Africa page: [SolarEdge ZA](https://www.solaredge.com/za/products/software-tools/mysolaredge)

---

### 6. Sigenergy
- **Market position:** Emerging premium segment; AI-powered all-in-one systems
- **Key strengths:** Integrated AI energy management, sleek all-in-one design, high energy density
- **API availability:** 🔶 Limited public info — likely proprietary cloud; community integrations still emerging
- **Note:** Worth monitoring as they grow market share in SA

---

### 7. Solis (Ginlong Technologies)
- **Market position:** Well-regarded mid-market; strong local support
- **Popular models:** Solis S6 series (hybrid), Solis RHI series
- **Key strengths:** Feature-rich, cost-effective, reliable
- **App/Portal:** SolisCloud app + portal at [soliscloud.com](https://www.soliscloud.com)
- **API availability:** ✅ SolisCloud API available
  - Requires registration; REST API with HMAC-SHA1 authentication
  - Community: Active Home Assistant and openHAB integrations

---

## Monitoring & Management Apps/Platforms

| Platform | Compatible Brands | App | API | Local Access | Notes |
|---|---|---|---|---|---|
| **Sunsynk Connect / PowerView** | Sunsynk | iOS, Android | ✅ REST | ❌ Cloud only | Official Sunsynk portal |
| **Solarman Smart** | Deye, Sofar, others | iOS, Android | ✅ REST | ❌ Cloud only | Used by many Chinese brands |
| **VRM Portal** | Victron | iOS, Android | ✅ Full REST | ✅ MQTT/Modbus | Best-in-class; open ecosystem |
| **mySolarEdge** | SolarEdge | iOS, Android | ✅ REST | ❌ Cloud only | Read-only API |
| **ShinePhone / ShineServer** | Growatt | iOS, Android | ✅ REST | ✅ Modbus | |
| **SolisCloud** | Solis | iOS, Android | ✅ REST | ✅ Modbus | HMAC auth |
| **Solar Assistant** | Sunsynk, Deye, Victron, Growatt, many more | iOS, Android, Web | ✅ MQTT | ✅ Local | Raspberry Pi-based; $50 one-time; 2-second polling |
| **Home Assistant** | All major brands | Web | Via integrations | ✅ Local+Cloud | Self-hosted; most flexible option |
| **PVOutput** | Generic | Web | ✅ REST | ❌ | Community logging/benchmarking platform |

---

## Third-Party / Integration Tools Relevant to WattCast

### Solar Assistant (`solar-assistant.io`)
- Raspberry Pi software that bridges inverter data to MQTT and web dashboard
- Supports **60+ inverter models** including all major SA brands
- Real-time data every 2 seconds
- Exposes MQTT topics — easy to consume in any backend
- One-time cost ~$50 USD; no subscriptions
- **Highly relevant for WattCast** as a data source layer

### Home Assistant Integrations
- [Deye/Sunsynk add-on](https://community.home-assistant.io/t/deye-sunsynk-inverter-integration-add-on/544048)
- [Victron Remote Monitoring](https://www.home-assistant.io/integrations/victron_remote_monitoring/)
- Growatt, Solis, SolarEdge all have community integrations

### PyPI Packages
| Package | Brand |
|---|---|
| `sunsynk-api-client` | Sunsynk |
| `sunsynkloggerapi` | Sunsynk logger |
| `solarman` (hareeshmu) | Deye/Solarman |
| `victronenergy/vrm-api-python-client` | Victron |

---

## API Capability Summary (for WattCast integration planning)

| Brand | Read Data | Change Settings | Local (no cloud) | Auth Method |
|---|---|---|---|---|
| Sunsynk | ✅ | ✅ | ❌ | OAuth2 / token |
| Deye (Solarman) | ✅ | 🔶 Limited | ✅ RS485/Modbus | API key + secret |
| Victron | ✅ | ✅ | ✅ MQTT/Modbus | Bearer token |
| Growatt | ✅ | 🔶 Limited | ✅ Modbus | API key |
| SolarEdge | ✅ | ❌ (read-only) | ❌ | API key |
| Solis | ✅ | 🔶 | ✅ Modbus | HMAC-SHA1 |

---

## Installer Account & Write Permissions (Critical for WattCast)

This is the most important operational detail for WattCast — changing inverter settings via API requires elevated installer permissions, not just a standard user account.

### Sunsynk
- Default accounts created on sunsynk.net are **view-only**.
- To get write/settings-change access, the installer must request an elevated role via the **User Level Access Change form** at [sunsynk.org/remote-monitoring](https://www.sunsynk.org/remote-monitoring).
- Roles: Standard → Installer → Approved Installer → Advanced User.
- Approved within ~24 working hours.
- **Plant Sharing:** Installers can be linked to customer plants via the Sunsynk Connect "Plant Ownership/Sharing" feature — the installer's elevated account then has write access to that customer's plant.
- API endpoint for settings: `https://openapi.sunsynk.net` (same API used by the app).

### Deye (via Solarman)
- Remote configuration requires an **installer account with remote control activation**.
- Process: Email `service@deye.com.cn` with logger serial number and inverter serial number to request remote control activation.
- Once activated, the installer sees a "Remote Control" button in the Solarman app and this capability is also exposed via the Solarman Business API.
- Data refresh interval can be reduced to 1 minute on request (default is longer).
- Solarman Business API access: login to the [SolarMAN Business platform](https://doc.solarmanpv.com/en/Documentation%20and%20Quick%20Guide) and request API key/secret.

### Implication for WattCast
WattCast must support installers providing their **elevated API credentials** per brand (Sunsynk installer token, Solarman business API key). WattCast then acts on behalf of the installer against the inverter API when executing rules. Customer accounts in WattCast are separate from inverter brand accounts.

---

## Smart Switch Integration

WattCast will support any switch that exposes a **webhook or documented REST API**. Primary use cases: geysers (30A) and pumps.

### Shelly (Recommended — Best API)
- **API:** Fully documented REST + webhook at [shelly-api-docs.shelly.cloud](https://shelly-api-docs.shelly.cloud/)
- **Local + Cloud:** Can be called via local IP (no internet required) or via Shelly Cloud
- **Webhooks:** Devices can push state-change events to WattCast (switch.on, switch.off, power change)
- **Control:** `HTTP GET /relay/0?turn=on` (Gen1) or RPC JSON calls (Gen2)
- **SA availability:** Widely available; popular in SA solar/home automation community
- **Suitable for:** 30A geyser switches, pool pumps, general loads
- **Verdict:** ✅ Best choice for WattCast — most open, local + cloud, excellent docs

### Tuya / Smart Life
- **API:** [Tuya Developer Platform](https://developer.tuya.com/en/docs/iot/) — cloud REST API with OAuth2
- **SA availability:** Very common — many cheap SA geyser smart switches are Tuya-based (30A, 20A variants)
- **Webhooks:** Supported via Tuya IoT Platform (requires developer account)
- **Local:** Possible via `tuyapi` library but not officially documented — cloud preferred
- **Caveat:** Requires Tuya IoT developer account and app registration; slightly more setup than Shelly
- **Verdict:** ✅ Worth supporting — huge installed base in SA, especially budget switches

### Sonoff
- **API:** Sonoff does NOT have native local REST API in stock firmware
- **Cloud:** eWeLink cloud API available but less open than Shelly/Tuya
- **Webhooks:** Limited; requires eWeLink developer account
- **Verdict:** 🔶 Lower priority — less open ecosystem; skip unless customer demand is high

### Comparison for WattCast

| Brand | Local API | Cloud API | Webhook Push | SA Availability | Priority |
|---|---|---|---|---|---|
| **Shelly** | ✅ Excellent | ✅ Yes | ✅ Yes | Good | 🥇 First |
| **Tuya/Smart Life** | 🔶 Unofficial | ✅ Yes | ✅ Yes | Excellent | 🥇 First |
| **Sonoff** | ❌ No | 🔶 Limited | 🔶 Limited | Good | 🥉 Later |

---

## Key Takeaways for WattCast

1. **Sunsynk and Deye are the dominant residential brands** — prioritise for initial integration. Both require installer-level API credentials with elevated permissions; this is a deliberate business process WattCast needs to support.
2. **Installer onboarding flow** must include credential linking: installers provide their Sunsynk and/or Solarman API tokens to WattCast, which then acts on their behalf.
3. **Off-grid customers** are fully supported — they simply won't have grid-charge rules; battery and load rules still apply.
4. **Shelly and Tuya** cover the vast majority of smart switch installations in SA and should be the first two switch integrations.
5. **Shelly's local API** is a major advantage — WattCast rules can fire even if the Shelly cloud is down, as long as the device is on the same network (though this requires a local agent or the rule engine to be on-site or on the local network).
6. **Local Modbus/RS485** is available on most inverter brands as a fallback — important for reliability but adds hardware complexity; likely a later-phase feature.
7. A **Solarman API** integration covers Deye and several other brands simultaneously — high leverage integration.

---

## Sources
- [Top 8 Solar Inverter Brands SA 2025 — TYCORUN](https://www.tycorun.com/blogs/news/top-8-solar-inverter-brands-in-south-africa)
- [Best Solar Inverters SA 2026 — Energy Bee](https://energybee.co.za/guides/best-solar-inverters-south-africa-2026)
- [Best Solar Inverter Systems SA 2026 — Monostat](https://monostat.co.za/best-solar-inverter-systems-south-africa-2026/)
- [Solar Assistant](https://solar-assistant.io/)
- [Sunsynk API Access — support.sunsynk.com](https://support.sunsynk.com/support/solutions/articles/103000380621-api-access)
- [Sunsynk API Client — PyPI](https://pypi.org/project/sunsynk-api-client/)
- [Demystifying the Sunsynk API — 4x4community](https://www.4x4community.co.za/forum/showthread.php/366452-Demystifying-the-Sunsynk-API)
- [Deye/Sunsynk HA Integration](https://community.home-assistant.io/t/deye-sunsynk-inverter-integration-add-on/544048)
- [Monitoring Deye via Inteless API — DIY Solar Forum](https://diysolarforum.com/threads/monitoring-sunsynk-deye-via-intelless-api.32512/)
- [Solarman API Docs](https://doc.solarmanpv.com/en/Documentation%20and%20Quick%20Guide)
- [VRM API Docs — Victron](https://vrm-api-docs.victronenergy.com/)
- [Victron VRM Python Client — GitHub](https://github.com/victronenergy/vrm-api-python-client)
- [Victron HA Integration](https://www.home-assistant.io/integrations/victron_remote_monitoring/)
- [SolarEdge Monitoring API (PDF)](https://knowledge-center.solaredge.com/sites/kc/files/se_monitoring_api.pdf)
- [mySolarEdge ZA](https://www.solaredge.com/za/products/software-tools/mysolaredge)
- [Solarman MQTT — GitHub](https://github.com/hareeshmu/solarman)
