# WattCast — Core Abstractions
**Status:** Draft v2 — May 2026
**Derived from:** Eschatologist automation system (Reitz, Free State)

---

## How to read this document

This document defines the conceptual building blocks of WattCast. Each abstraction is a named thing the system knows about and reasons over. Getting these right matters because they determine the data model, the API surface, the UI structure, and what installers can configure.

Abstractions are ordered from outermost to innermost — start with the big containers, work down to the fine-grained logic.

---

## 1. Plant

A **Plant** is a single customer installation. It is the top-level container for everything WattCast manages at one physical site.

A Plant has:
- One or more **Inverters**
- One or more **Managed Loads**
- A set of **Inverter Rules**
- A set of **Context Signals** (the data it reasons over)
- A set of **Notification Channels**
- Configuration settings (location, timezone, tariff zone)
- An owning **Installer** and one or more **Customer** users

Every other abstraction exists within the scope of a Plant. Rules, presets, and signals from one Plant are invisible to another.

**Plant is the top-level container — there is no "Site" above it.** A grouping layer above Plant (e.g. a property with multiple independent systems) is deliberately out of scope for v1. If the product proves itself and multi-system estates become a real use case, a Site layer can be introduced later without breaking the Plant model.

**WattCast is multi-plant from day one.** An installer manages a portfolio of Plants. The cloud holds canonical config for all of them; each Plant's edge device (Pi or customer HA) holds operational state and executes locally.

---

## 2. Inverter

An **Inverter** is a controllable solar hybrid inverter registered under a Plant. WattCast treats the inverter as a stateful device that can be read from and written to.

An Inverter has:
- A **brand/model** (Sunsynk, Deye/Solarman, Victron, etc.)
- A **connection method** (cloud API credentials, local Modbus address, etc.)
- A current **state** (live readings: battery SOC, PV power, grid power, load power, work mode)
- A set of **Presets** — named configurations that can be applied to it

The inverter is a *target* of automation, not an actor. Rules decide what to do; the inverter receives the instruction.

**Key insight from eschatologist:** PV power reported by the inverter is demand-limited — it throttles output when the battery is full. This means inverter PV readings are unreliable for deciding whether the sun is actually shining. A separate solar radiation signal (weather station or irradiance sensor) must be used for that decision.

---

## 3. Preset

A **Preset** is a named snapshot of inverter settings that can be applied as a unit.

A Preset contains:
- A human-readable **name** (e.g., "Cloudy Day", "Normal Evening", "Winter Grid Charge")
- A **description** of when and why it's used
- A **settings blob** — the full set of inverter parameters it configures (grid charge slots, SOC limits, time-of-use schedules, work mode, sell settings, etc.)
- The **inverter brand** it targets (presets are brand-specific; a Sunsynk preset cannot be applied to a Victron)

Presets are **whole-state replacements**, not partial patches. Applying a preset pushes all its settings to the inverter atomically. This is intentional: partial updates create ambiguous intermediate states that are hard to reason about.

Presets are managed by the installer. Customers can view which preset is active but cannot modify presets.

**Why presets instead of individual settings:** In practice, inverter settings are interdependent. Changing the grid charge window without also adjusting SOC targets and work mode produces nonsensical configurations. A preset bundles the settings that belong together.

---

## 4. Managed Load

A **Managed Load** is a controllable electrical device (other than the inverter) that WattCast can turn on or off. The canonical example is a geyser controlled via a Shelly relay.

A Managed Load has:
- A **name** and **type** (geyser, pool pump, EV charger, general switch, etc.)
- A **controller device** (e.g., Shelly Plus 1PM at a local IP address)
- A **current state** (on/off, live power reading if available)
- A **mode** — one of three:

  **Auto** — the divert logic runs normally. Hardware fallback schedules are active on the controller device. This is the hands-off default.

  **Force ON** — relay is forced on immediately. Divert logic is bypassed. Hardware schedules on the controller are disabled so they cannot override the forced state.

  **Force OFF** — relay is forced off. Divert logic bypassed. Hardware schedules disabled.

- A **divert policy** — the conditions under which Auto mode turns the load on/off (see Divert Logic below)
- A set of **Fallback Schedules** — timer schedules stored on the controller device itself, active when the automation system is offline

**The three-mode pattern is a core WattCast UI primitive.** Every managed load has this exact mode toggle. Force ON/OFF exist specifically because users need manual override — when you have guests, when the geyser is already hot, when you're troubleshooting. The Auto mode always re-enables hardware fallback schedules to ensure the load still functions if the cloud or Pi goes offline.

**Fallback schedules are first-class, not an afterthought.** SA internet and power are unreliable. A load that only works when WattCast is online is not safe to deploy. Every managed load must have a hardware fallback, and WattCast manages those fallback schedules on the controller device directly.

---

## 5. Divert Logic

**Divert Logic** is the decision-making policy for a Managed Load in Auto mode. It decides when to turn the load on and off based on live signals.

Divert Logic is defined by:
- An **ON condition** — the full set of conditions that must be true to turn the load on
- An **OFF condition** — the conditions that must be true to turn the load off
- A **hysteresis gap** — ON and OFF thresholds are deliberately different to prevent rapid relay flapping

The canonical geyser example:
- **ON when:** battery SOC ≥ 98% AND solar radiation ≥ 650 W/m²
- **OFF when:** battery SOC < 90% OR solar radiation < 650 W/m²
- **Hysteresis:** 8% SOC gap between ON (98%) and OFF (90%)

**Why solar radiation instead of PV power:** The inverter throttles PV output when demand is low, so PV power readings understate available sunshine. The weather station's radiation sensor gives the true, unthrottled reading and must be used for load divert decisions.

Divert Logic runs on a fast cycle (every 2 minutes in eschatologist). It is a tight feedback loop, not a scheduled event.

---

## 6. Context Signal

A **Context Signal** is any real-time or forecast data value that automation logic can reference. Signals are the inputs; rules and divert logic are the consumers.

Signals fall into four categories:

**Live device signals** — read from inverter and controller devices in near-real-time:
battery SOC, PV power, grid power, load power, battery power, work mode, relay state, switch power reading

**Live weather signals** — from a weather station at or near the site:
solar radiation, temperature, humidity, wind speed, rain rate, UV index, soil moisture

**Forecast signals** — from an ECMWF or similar forecast API, updated on a schedule:
solar hours today/tomorrow, expected generation today/tomorrow, solar GTI today/tomorrow, rain probability (3hr, daily), weather severity (0–6 scale), min/max temperature tomorrow, cloud cover

**Time signals** — derived from the system clock:
hour, minute, day of week, month, day of year, solar hours remaining today

**Not in scope: tariff and time-of-use pricing signals.** South Africa's electricity pricing is too fragmented to model reliably — rates vary by municipality, direct Eskom supply vs. municipal resale, and most residential customers have flat-rate (not TOU) tariffs. Tariff-aware optimisation is a future consideration if Eskom or municipalities move toward standardised TOU pricing. The signal architecture is designed to accommodate this later without structural changes.

Signals are **lazily fetched** — a signal is only retrieved from its source when a rule or condition actually references it. This avoids unnecessary API calls on every evaluation cycle.

**Not all plants will have all signals.** A site without a weather station won't have solar radiation or soil moisture. A site without a FarmWeather subscription won't have ECMWF forecasts. WattCast must degrade gracefully — rules that reference unavailable signals are skipped or flagged, not silently misfired.

---

## 7. Inverter Rule

An **Inverter Rule** is a named automation that watches Context Signals and applies an Inverter Preset when its conditions are met.

A Rule has:
- A **name** and optional description
- A **priority** — integer; lower = evaluated first
- A **schedule** — either "daily at HH:MM" or "every N minutes"
- A **condition set** — one or more conditions, all of which must be true (AND logic)
- A **target preset** — which preset to apply when conditions match
- An **enabled toggle**

**Evaluation model: priority-ordered first-match-wins.** Rules are sorted by priority. The first rule whose schedule says it should run now AND whose conditions are all met wins. Its preset is applied and evaluation stops. No other rules fire.

This model is chosen because inverter presets are mutually exclusive — the inverter is in exactly one configuration state at a time. Allowing multiple rules to fire would create conflicts. An additive/independent rule model is appropriate for loads (multiple loads can be on simultaneously), but not for inverter state.

**Conditions** reference Context Signals by field name with a comparison operator and value:
```
battery_soc < 30
hour >= 17
solar_hours_remaining < 2
weather_severity_today >= 4
```

Conditions within a rule are AND'd. There is no OR within a rule — create two rules at adjacent priorities instead.

**Simulation mode** is a first-class feature. When enabled globally or per-rule, the engine logs what it *would* do without pushing settings to the inverter. New rules should default to simulated until the installer has verified their behaviour.

---

## 8. Scheduled Trigger

A **Scheduled Trigger** is a one-off automation that fires at a specific time or in response to a forecast condition. It is distinct from Inverter Rules (which run on a tight polling cycle) in that it fires once per day at a configured time.

The canonical example is the **Cloudy Day Preset Loader**: every morning at 07:00, check the day's forecast solar hours. If below threshold, load the "Cloudy Day" preset.

A Scheduled Trigger has:
- A **name**
- A **fire time** (daily at HH:MM)
- A **condition** (optional — if absent, fires unconditionally)
- An **action** (typically: load a preset, send a notification, or both)
- An **enabled toggle**

Scheduled Triggers complement Inverter Rules. Rules handle ongoing real-time adjustments (every 2 min); Scheduled Triggers handle once-daily context switches (set the day's strategy based on the morning forecast).

**Why separate from Rules:** The cloudy day check could theoretically be expressed as an inverter rule with a `solar_hours_remaining < 3` condition. In practice this causes problems — it would re-evaluate every 2 minutes and potentially re-load the preset repeatedly. A once-daily trigger is simpler and produces a stable, predictable outcome.

---

## 9. Notification

A **Notification** is an alert sent to a user when a significant event occurs.

A Notification has:
- An **event type** — a fixed taxonomy of things WattCast can notify about
- A **channel** — where to send it (Telegram, email, push notification, etc.)
- A **cooldown** — minimum time between repeated sends of the same event type
- An **enabled toggle** per event type
- Optional **threshold** — for value-based events (e.g., battery low at < 20%)

The **event taxonomy** (derived from eschatologist):
- grid_lost / grid_restored
- battery_low / battery_recovered
- inverter_preset_loaded (rule-triggered)
- scheduled_preset_loaded (scheduled trigger)
- load_turned_on / load_turned_off
- load_mode_changed (Auto/Force ON/Force OFF)
- device_offline (inverter, load controller, weather station)
- automation_error

**Cooldown-based deduplication** is the dedup strategy. Rather than tracking explicit state transitions (which requires knowing prior state), the system checks: was this same event type successfully sent within the cooldown window? If yes, skip. This handles rapid flapping events naturally (e.g., grid cycling on/off every few minutes during load-shedding) without notification storms.

---

## 10. Actor

An **Actor** is a person or system that can read or modify Plant configuration. WattCast has three Actor roles:

**Installer** — the solar installation company. Can create and manage Plants, configure inverters, define presets and rules, set notification thresholds, and view all data. Cannot view data from other installers' plants.

**Customer** — the homeowner. Read-only access to their own Plant: live status, activity logs, which preset is active, notification history. Can change load modes (Auto/Force ON/OFF) on their own Managed Loads. Cannot modify rules, presets, or inverter settings.

**WattCast Platform** — the automation engine itself. Runs rules, applies presets, manages fallback schedules, sends notifications. Acts on behalf of the Plant autonomously.

**Why customers cannot modify rules:** The product is a managed service. Installers are responsible for correct configuration. Allowing customers to modify rules creates liability and support complexity. The mode toggle (Auto/Force ON/OFF) on each load gives customers the control they actually need day-to-day without exposing the underlying automation.

---

## 11. Execution Log

An **Execution Log** is a time-series record of every automation action the system takes or considers taking.

Every rule evaluation produces a log entry regardless of outcome:
- **applied** — rule matched, preset pushed to inverter
- **simulated** — rule matched, but simulation mode is on (no push)
- **no_match** — no eligible rule had all conditions met
- **already_active** — the matching rule's preset is already loaded (no-op)
- **error** — an API call or condition evaluation failed

Each entry captures: timestamp, rule name, preset name, result, the condition values at evaluation time (data snapshot), and any error message.

Similarly, every Managed Load action produces a log entry: timestamp, action (on/off/mode_change), reason (which condition triggered it), and live signal values at the time.

Execution Logs are the primary debugging tool. When an installer asks "why didn't it charge last night?", the log answers the question without guesswork.

---

## Abstraction Relationships

```
Plant
├── Inverter(s)
│   └── Presets (named configurations)
├── Managed Load(s)
│   ├── Divert Logic (on/off conditions + hysteresis)
│   └── Fallback Schedules (on the controller device)
├── Inverter Rules (priority-ordered, first-match-wins)
│   ├── Conditions (reference Context Signals)
│   └── Target Preset
├── Scheduled Triggers (daily, forecast-driven)
├── Context Signals
│   ├── Live device (battery SOC, PV power, ...)
│   ├── Live weather (solar radiation, temp, ...)
│   ├── Forecast (solar hours, GTI, severity, ...)
│   └── Time (hour, day of week, ...)
├── Notifications
│   ├── Event taxonomy
│   ├── Channels (Telegram, email, ...)
│   └── Cooldown / dedup rules
├── Execution Logs
└── Actors
    ├── Installer (configure everything)
    └── Customer (read + load mode toggle)
```

---

## Key Design Principles

**Local execution, cloud configuration.** Cloud holds canonical config. Edge device (Pi or customer HA) executes. Automation must continue working during internet outages.

**Hardware fallbacks are mandatory.** Every Managed Load must have controller-side fallback schedules. Automation adds intelligence; it does not replace the underlying device's ability to function independently.

**Presets are atomic.** Inverter state is changed by applying a whole preset, never by patching individual settings. This keeps configuration coherent and auditable.

**First-match-wins for inverter, independent for loads.** The inverter can only be in one state — rules are mutually exclusive. Multiple loads can run simultaneously — each load has its own independent divert logic.

**Simulation before live.** Every rule and trigger defaults to simulated. The installer must explicitly enable live mode after verifying behaviour in the logs.

**Signals degrade gracefully.** Missing signals (no weather station, no forecast subscription) cause affected rules to be skipped, not to misfire. The system should always prefer doing nothing over doing the wrong thing.

**Logs answer "why."** Every automation action, considered or taken, is logged with the signal values that drove the decision. Debugging never requires guesswork.

---

## Deferred decisions

**Site layer** — no grouping above Plant in v1. Revisit if multi-system estate management becomes a real use case.

**Tariff / TOU pricing** — SA pricing is too fragmented (municipality, direct Eskom vs. resale, mostly flat-rate) to model usefully now. Architecture should accommodate this as a future signal category if standardised TOU pricing emerges.

---

*Next: data model (field-level detail for each entity), then API surface and installer/customer permission model.*
