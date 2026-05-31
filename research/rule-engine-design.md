# WattCast Rule Engine — Design Document

**Last updated:** 2026-05-28 (rev 2)

---

## Overview

WattCast's rule engine is the core of its value proposition. It extends Solar Assistant's
basic automation model with predictive, future-aware conditions that SA will never have —
loadshedding schedule awareness, PV forecasting, weather data, and smart switch control.

---

## Design Principles

1. **Installer has full control** — all rules are configurable, including the core logic.
   WattCast ships with sensible defaults but nothing is locked.
2. **Homeowner can view, not edit** — rules are visible to the homeowner for transparency,
   but only the installer (or WattCast admin) can modify them.
3. **First rule wins** — when multiple rules target the same setting simultaneously,
   the highest-priority rule (lowest index / topmost in the list) takes effect.
   Priority ordering is a future discussion.
4. **Hard safety limits always apply** — regardless of what a rule says, WattCast will
   never write values outside safe bounds (e.g. never below output_shutdown_capacity).
5. **Default rules work out of the box** — a new install with default rules should
   immediately provide value without any installer configuration.

---

## Rule Structure

Inspired by Solar Assistant's automation model, extended with predictive conditions.

Each rule has:
- **Name** — human-readable label (e.g. "Pre-loadshedding charge boost")
- **When** — one or more trigger conditions (AND logic between conditions)
- **Set** — what to change and what value to set it to
- **Priority** — order in which conflicting rules are resolved (lower = higher priority)
- **Enabled/Disabled** toggle

### Example rule (natural language)
> **When** loadshedding starts in less than 3 hours  
> **And when** battery SoC < 80%  
> **And when** PV forecast remaining today > 1 kWh  
> **Set** Capacity point 1–6 = 80%

---

## Condition Palette ("Based on which condition(s)?")

### Current state conditions (SA has these — WattCast inherits)
| Condition | Type | Example |
|---|---|---|
| Time of day | Time range | 00:00 to 06:00 |
| Day of week | Multi-select | Mon, Tue, Wed |
| Month of year | Multi-select | Jun, Jul, Aug (winter) |
| Battery state of charge | Range % | 0% to 25% |
| Battery voltage | Range V | 44V to 48V |
| Grid voltage | Range V | 0V to 180V (outage detection) |
| Grid frequency | Range Hz | 49.5 to 50.5 |
| PV energy progress today | Range kWh | 0 to 5 kWh |
| PV energy remaining today | Range kWh | < 2 kWh |

### Predictive conditions (WattCast-exclusive — SA will never have these)
| Condition | Type | Example |
|---|---|---|
| Loadshedding starts in | Duration | < 3 hours |
| Loadshedding ends in | Duration | < 1 hour |
| Loadshedding duration | Duration | > 2 hours |
| Loadshedding stage | Range | >= 2 |
| Loadshedding active | Boolean | Yes / No |
| Next loadshedding slot today | Boolean | Yes / No |
| PV forecast tomorrow | Range kWh | < 5 kWh (cloudy day) |
| PV forecast today remaining | Range kWh | > 3 kWh |
| Current irradiance | Range W/m² | > 400 W/m² |
| Last 30min avg irradiance | Range W/m² | < 100 W/m² (cloud cover) |
| Weather condition | Select | Cloudy / Clear / Rain |
| Season | Select | Summer / Winter / Shoulder |

### Compound conditions
Multiple conditions can be combined with AND logic (same as SA's "And when" model).
OR logic is achieved by creating multiple rules targeting the same setting.

---

## Action Palette ("Which setting do you want to control?")

### Inverter settings — Safe (green)
Standard operational settings, safe to automate:
- Capacity point 1–6 (battery discharge floor per TOU slot)
- Charge point 1–6 (charge target per TOU slot)
- Grid charge point 1–6
- Start battery discharge capacity / voltage
- Stop battery discharge capacity / voltage
- Start grid charge capacity / voltage
- Use timer (on/off)
- Work mode
- Energy pattern
- Grid charge (on/off)
- Solar export when battery full
- Time point 1–6
- Program point 1–6
- Sell point 1–6
- Power point 1–6

### Inverter settings — Caution (⚠️ warning shown)
Can cause unexpected behaviour if set incorrectly:
- Max charge current
- Max discharge current
- Max grid charge current
- Max sell power
- Max solar power
- Output shutdown capacity
- Output shutdown voltage
- Grid voltage high/low
- Grid frequency high/low
- Grid peak shaving / Grid peak shaving power
- Grid trickle feed
- Battery float charge voltage
- Battery absorption charge voltage
- Battery equalization charge voltage

### Inverter settings — Restricted (🔒 installer-only or hidden)
Protocol/hardware level — not exposed to rule engine by default:
- Battery type
- Battery operation
- Lithium protocol
- Auxiliary port
- Remote switch
- Generator settings (unless site has a generator)
- Battery capacity (set once at install, not automated)

### Smart switches (WiFi-enabled) — WattCast extension
WattCast extends the action palette beyond inverter settings to include WiFi smart switches.
This is a key differentiator — SA cannot control external devices.

**Target device types:**
- Geyser / water heater (high priority — major load, 2–3kW)
- Pool pump
- EV charger
- Air conditioner
- Irrigation system
- Any other WiFi smart plug / switch

**Supported protocols — Phase 1 (webhooks + MQTT):**
- **Webhooks** — device calls a WattCast HTTP endpoint on state change; WattCast calls device HTTP endpoint to control it. No special protocol needed.
- **MQTT** — device publishes state to MQTT broker; WattCast publishes to device control topic. Since WattCast already has MQTT running, this is nearly free to add.

Devices covered by Phase 1:
- **Shelly** — webhooks + MQTT natively, no cloud dependency, local-first ✅
- **Sonoff with Tasmota** — MQTT natively ✅
- **Sonoff stock firmware** — webhooks ✅
- **Many Tuya devices** — MQTT via local API or Tasmota flash ✅

**Phase 2 (future):**
- Matter / Thread
- Home Assistant integration
- Tuya cloud API (for devices that can't be flashed)

**Example smart switch rules:**
- "When loadshedding active → turn off geyser"
- "When PV power > 2kW AND battery SoC > 80% → turn on geyser"
- "When grid outage detected → turn off pool pump"
- "When PV forecast tomorrow < 3kWh → turn on geyser now while sun available"

---

## UI Design Considerations

### Installer view (full access)
- Add / edit / delete / reorder rules
- Enable / disable individual rules
- Set rule priority (drag to reorder, or explicit priority number)
- See rule execution history (when did it last fire, what did it set)
- Configure site parameters (battery size, load profile, loadshedding area)

### Homeowner view (read-only)
- See active rules and their current status
- See what WattCast last did and why ("Raised capacity point to 80% — loadshedding in 2h")
- See current inverter state and smart switch states
- Cannot edit rules (can request changes via installer)

### Rule conflict display
- When two rules conflict, show which rule "won" and why
- Highlight disabled rules that would have fired

---

## SA Automation Model vs WattCast Rule Engine

| Feature | Solar Assistant | WattCast |
|---|---|---|
| Time of day conditions | ✅ | ✅ |
| Battery SoC conditions | ✅ | ✅ |
| Grid/PV conditions | ✅ | ✅ |
| Loadshedding awareness | ❌ | ✅ |
| PV forecast conditions | ❌ | ✅ |
| Weather conditions | ❌ | ✅ |
| Smart switch control | ❌ | ✅ |
| Compound AND conditions | ✅ | ✅ |
| Compound OR conditions | ❌ (workaround) | ✅ (multiple rules) |
| Homeowner visibility | ❌ | ✅ (read-only) |
| Multi-site management | ❌ | ✅ (future) |
| Rule priority/conflict resolution | ❌ | ✅ |

---

## Rule Set Portability

A key installer workflow feature — build once, deploy many times.

### Export
- Export a site's full rule set as a JSON/YAML file
- Includes all rules, priorities, parameters, and device mappings
- Installer can save named templates (e.g. "Small household 5kWh no generator")

### Import
- Import a rule set file onto a new site
- WattCast prompts to map template device names to actual local devices
  (e.g. "Geyser" → select from discovered MQTT/webhook switches)
- Existing rules can be kept, replaced, or merged

### Reset to defaults
- "Wipe and reinstate default rule set" option — removes all custom rules,
  reinstates the factory defaults
- Requires installer-level confirmation
- Useful when a customer's rules are in a broken/conflicting state

### Template library
- WattCast ships with a built-in template library
- Installer can contribute/save their own templates
- Future: shared template marketplace across installers

---

## Default Rule Set

Priority-ordered rules that ship with every WattCast install. All can be edited or disabled.
Loadshedding rules are included but low priority since loadshedding has been suspended since
March 2025 — they will activate automatically when loadshedding returns.

### Rule 1 — Solar surplus geyser (Priority 1 — highest impact daily)
> **When** PV power > load power + 500W  
> **And when** Battery SoC > 80%  
> **And when** Time of day 08:00–16:00  
> **Set** Geyser switch = ON

Rationale: Biggest daily saving for most households. A 3kW geyser running 2hrs on free
solar instead of grid = meaningful monthly bill reduction. 500W buffer ensures battery
still charges while geyser runs.

### Rule 2 — Geyser off at night / grid
> **When** PV power < 200W  
> **Set** Geyser switch = OFF

Rationale: Ensure geyser never runs on battery or grid unnecessarily.

### Rule 3 — Grid outage load shedding (unplanned outages)
> **When** Grid voltage < 180V  
> **Set** Pool pump = OFF  
> **Set** Geyser = OFF  
> **Set** EV charger = OFF

Rationale: Unplanned outages still happen regardless of loadshedding schedule.
Immediately shed non-essential loads to extend battery life.

### Rule 4 — Overnight battery protection
> **When** Time of day 22:00–06:00  
> **And when** Battery SoC < 30%  
> **Set** Capacity point 1–6 = 25%

Rationale: Prevent battery from draining to shutdown overnight. 25% floor gives
enough buffer for morning until solar kicks in.

### Rule 5 — Winter morning grid top-up
> **When** Month of year Jun, Jul, Aug  
> **And when** Time of day 06:00–08:00  
> **And when** Battery SoC < 50%  
> **And when** PV forecast today < 5 kWh  
> **Set** Grid charge = ON  
> **Set** Capacity point 1 = 60%

Rationale: On cloudy winter mornings, charge from grid during off-peak before
morning load spike. Only fires when solar forecast suggests poor generation.

### Rule 6 — Solar peak optimiser
> **When** Time of day 10:00–14:00  
> **And when** PV power > 3000W  
> **Set** Capacity point 1–6 = 20%

Rationale: During peak solar hours, lower the discharge floor so the battery
absorbs as much solar as possible rather than exporting to grid at low value.

### Rule 7 — Pre-loadshedding charge boost (inactive until loadshedding returns)
> **When** Loadshedding starts in < 3 hours  
> **And when** Battery SoC < 80%  
> **Set** Capacity point 1–6 = 80%  
> **Set** Grid charge = ON

Rationale: Ensure battery is sufficiently charged before a loadshedding slot.
Target SoC calculated from slot duration × estimated load / battery capacity.
Currently dormant — will activate automatically when loadshedding resumes.

### Rule 8 — Post-loadshedding recovery (inactive until loadshedding returns)
> **When** Loadshedding active = No (just ended)  
> **And when** Time of day 06:00–18:00  
> **Set** Grid charge = OFF  
> **Set** Capacity point 1–6 = 60%

Rationale: After loadshedding ends, restore normal settings and let solar
recover the battery naturally rather than continuing to grid charge.

---

## Open Questions

1. ~~**Smart switch protocol priority**~~ **DECIDED:** Webhooks + MQTT first. Matter/Thread in Phase 2.
2. **Rule priority mechanism** — drag-to-reorder vs explicit number vs timestamp?
3. **OR logic** — is "create two rules" sufficient or do we need explicit OR operators?
4. ~~**Default rule set**~~ **DECIDED:** See Default Rule Set section above.
5. **Rule templates** — installer selects from pre-built templates and customises?
6. **Homeowner override** — can homeowner temporarily pause a rule? (e.g. "pause for today")
7. **Rule scheduling** — can rules be active only on certain days/dates (e.g. seasonal rules)?
8. **Geyser SoC target calculation** — how does WattCast calculate the right capacity point
   for loadshedding? Formula: (slot_duration_hrs × avg_load_W) / battery_capacity_Wh × 100 + safety_margin
9. **Webhook security** — how do we authenticate inbound webhooks from switches?

---

## Next Steps

1. Design the default rule set for a typical install
2. Investigate Tuya local API vs Shelly for smart switch integration
3. Build the rule engine data model (Python dataclasses or similar)
4. Build a simple web UI for rule management (tech stack TBD)
5. Wire up first real rule: "Pre-loadshedding capacity boost"
