# Loadshedding Data Sources — Research & Abstraction Design

**Researched:** 2026-05-27

---

## Current State of Loadshedding in SA

As of 2026-05-27, loadshedding has been suspended for **378 days** (Stage 0). However WattCast should be
designed to handle it returning at any time. The abstraction layer below ensures the data source
can be swapped with minimal code changes.

---

## Reitz-Specific Facts

- **Area:** Reitz town, Nketoana municipality, Free State
- **Block:** 14 (municipally billed, Eskom-supplied — NOT Eskom Direct; Petsana township adjacent to Reitz IS Eskom Direct)
- **Type: Even (E)** — schedule slots start at even hours: 00:00, 02:00, 04:00, 06:00... (no 1-hour offset)
- **Confirmed by:** ourpower.co.za, loadshedding.com, ESP area search, Eskom official Reitz.pdf
- **Official PDF:** `https://www.eskom.co.za/distribution/wp-content/uploads/2021/06/Reitz.pdf` — **LIVE as of 2026-05-27**, found via Eskom Distribution Free State municipal schedules page under Nketoane section
- **ESP area ID:** Run `/areas_search?text=reitz` to confirm exact ID
- **eskom-calendar area name:** Filter CSV for `area_name` containing `reitz` to confirm exact string

### Reitz PDF Schedule Structure

The PDF uses a static monthly schedule (apply each month; drop day 31 for 30-day months, days 29-31 for Feb).

| Time Slot | | Block numbers by day of month (1–31) |
|---|---|---|
| 00:00 | 02:30 | Blocks cycling by day |
| 02:00 | 04:30 | " |
| 04:00 | 06:30 | " |
| 06:00 | 08:30 | " |
| 08:00 | 10:30 | " |
| 10:00 | 12:30 | " |
| 12:00 | 14:30 | " |
| 14:00 | 16:30 | " |
| 16:00 | 18:30 | " |
| 18:00 | 20:30 | " |
| 20:00 | 22:30 | " |
| 22:00 | 00:30 | " |

- 12 time slots per day × 2.5 hours each = full 24-hour coverage with 30-min overlap (stage cascade applies)
- Block 14 shed times per day determinable by reading the block column for that date row
- Hierarchy shown in PDF rows: Province → Free State → City/Munic → Nketoane → Suburb/Town → Reitz

---

## Data Source Landscape (as of 2026-05-27)

| Source | Status | Notes |
|---|---|---|
| Eskom Excel files (2022 paths) | **Dead — 404** | Was the gold standard raw data |
| Eskom loaddocs/ Excel files | **Dead — 404** | Moved/removed |
| Eskom loadshedding.co.za search API | **Live but returns empty** | Only populates during active loadshedding |
| Eskom Reitz.pdf (2021) | **Live — confirmed 2026-05-27** | Per-town PDF schedule, parseable with tabula; URL: eskom.co.za/.../2021/06/Reitz.pdf |
| ourpower.co.za | Live, **API now paid** | Application-only, email for access |
| EskomSePush (ESP) | **Paid** ($55+/yr personal) | Most reliable, most maintained |
| eskom-calendar CSV | **Archived** but still downloadable | Has historical schedule, Block 14 data in there |

---

## Option 1: EskomSePush API (Paid — use if loadshedding returns seriously)

**Base URL:** `https://developer.sepush.co.za/business/2.0`  
**Auth:** `token: <API_KEY>` request header  
**Register:** https://eskomsepush.gumroad.com/l/api  
**Pricing (yearly):**
- Personal: $55+/year — 50 req/day, personal use only
- Professional: $135/year — 200 req/day, internal use only
- Business: $6,000/year — 2,500 req/day
- There appears to be a limited free monthly tier but with very tight quota

**Status: Already a paid service — NOT the default for WattCast.**  
Consider subscribing ($55/year ≈ R1,000/year) only if loadshedding returns at significant stages.

### Endpoints

| Method | Path | Description | Quota? |
|---|---|---|---|
| GET | `/status` | National stage + next stage changes | Yes |
| GET | `/area?id=<id>` | Full schedule for a specific area (next events) | Yes |
| GET | `/area?id=<id>&test=current` | Mock response at current stage (for dev/testing) | **No** |
| GET | `/area?id=<id>&test=future` | Mock response at future stage (for dev/testing) | **No** |
| GET | `/areas_search?text=<query>` | Find area_id by suburb name | Yes |
| GET | `/areas_nearby?lat=<lat>&lon=<lon>` | Find areas by GPS coordinates | Yes |
| GET | `/topics_nearby?lat=<lat>&lon=<lon>` | User-reported nearby outages | Yes |
| GET | `/allowance` | Check remaining daily quota | **No** |

### Example responses

**GET /status**
```json
{
  "status": {
    "capetown": {"name": "Cape Town", "next_stages": [...], "stage": "2"},
    "eskom": {"next_stages": [...], "stage": "0"}
  }
}
```

**GET /area?id=eskde-10-reitzvilleresidentie**
```json
{
  "events": [
    {"end": "2023-08-12T22:30:00+02:00", "note": "Stage 4", "start": "2023-08-12T20:00:00+02:00"}
  ],
  "info": {"name": "Reitzville Residentie", "region": "Eskom Direct, Reitzville"},
  "schedule": {
    "days": [
      {"date": "2023-08-12", "name": "Saturday", "stages": [["20:00-22:30"], [], [], ...]}
    ],
    "source": "https://loadshedding.eskom.co.za/"
  }
}
```

### Usage strategy for WattCast (50 calls/day budget)
- **On startup:** Search for area_id once, cache it permanently → 1 call (one-time)
- **Stage check:** Poll `/status` every 30 min → ~48 calls/day
- **Schedule fetch:** Poll `/area` once per hour or on stage change → ~24 calls/day
- **Quota check:** Call `/allowance` on startup → 0 quota cost
- Use `/area?test=current` during development — doesn't cost quota

### Reitz area ID
Run once: `GET /areas_search?text=reitz` to find the correct `area_id` for Reitz, ZA.

---

## Option 2: eskom-calendar CSV (Default — free, open source)

**URL:** `https://github.com/beyarkay/eskom-calendar/releases/download/latest/machine_friendly.csv`  
**Format:** CSV with columns: `area_name, stage, start, finsh, source`  
**Status:** Project archived (loadshedding stopped). May not be updated if loadshedding returns.

```python
import pandas as pd
url = "https://github.com/beyarkay/eskom-calendar/releases/download/latest/machine_friendly.csv"
df = pd.read_csv(url, parse_dates=['start', 'finsh'])
reitz = df[df['area_name'].str.contains('reitz', case=False)]
```

**Pros:** No API key, no quota, direct from GitHub  
**Cons:** Archived project, may not be maintained if loadshedding returns

---

## Option 3: Eskom loadshedding.co.za Search API (Free — activates during loadshedding)

**Base URL:** `https://loadshedding.eskom.co.za/LoadShedding/`

| Endpoint | Description |
|---|---|
| `GET /GetSurburbData?pageSize=100&pageNum=1&searchTerm=reitz&id=2` | Search suburbs in province (id=2 = Free State) |
| `GET /GetStatus` | Current loadshedding status |

**Important:** Returns empty results when Stage 0. Will come alive when loadshedding resumes.
This is the best **free** option when loadshedding returns — no API key, direct from Eskom.

Province IDs: EC=1, FS=2, GP=3, KZN=4, LP=5, MP=6, NW=7, NC=8, WC=9

---

## Lessons from eskom-calendar Source Code

The eskom-calendar repo (`beyarkay/eskom-calendar`) parsed Eskom's official Excel files.
Key techniques worth reusing in WattCast's custom scraper:

### 1. Excel structure (parse_eskom_excel.py)
- Eskom published per-province Excel files with two key sheets:
  - **"Schedule"** sheet: rows 14–110, columns = `start, finsh, stage, 1..31` (days of month)
  - **"SP_List"** sheet: suburb → block mapping (`MP_NAME`, `SP_NAME`, `BLOCK`, `TYPE`)
- Files were at `https://www.eskom.co.za/distribution/wp-content/uploads/2022/09/<Province>_LS.xlsx`
- **These URLs are now 404** — Eskom removed them. May reappear when loadshedding returns.

### 2. Odd/Even area split
Eskom splits each block into odd (U) and even (E) start times:
- **Even (E):** slots start at 00:00, 02:00, 04:00...
- **Odd (U):** slots start at 01:00, 03:00, 05:00... (even + 1 hour)
- Reitz Block 14 will be either odd or even — check `TYPE` column in SP_List sheet

### 3. Stage cascade (subset_stages)
Stage 4 = affected at stages 1, 2, 3, AND 4.
Must expand: a stage-4 row → rows for stages 1, 2, 3, 4.
```python
def subset_stages(df):
    for stage in range(1, 8):
        this_stage = df[df.stage == stage].copy()
        this_stage.stage = stage + 1
        df = pd.concat((this_stage, df))
    return df
```

### 4. Overlapping slot merging (filter_duplicates)
Slots like `02:00–04:30` and `04:00–06:30` overlap — must merge to `02:00–06:30`.
Their algorithm: sort by stage+date+start_time, iterate and extend previous row's end time
if current row overlaps with it.

### 5. Per-town PDFs (parse_eskom.py)
Before the Excel approach, Eskom published individual town PDFs.
Reitz PDF: `https://www.eskom.co.za/distribution/wp-content/uploads/2021/06/Reitz.pdf`
Parsed with `tabula-py`. This URL may still be live — worth checking when needed.

---

## Option 4: Custom Scraper (Last resort — self-maintained)

Scrape directly from `https://loadshedding.eskom.co.za/` or the official Eskom app.  
**Cons:** Fragile (breaks on site changes), maintenance burden, may violate ToS.  
Only use if ESP goes fully paid and eskom-calendar stays unmaintained.

---

## Abstraction Layer Design

The key principle: **WattCast should never call ESP directly from business logic**.
All loadshedding data flows through a single `LoadsheddingProvider` interface.
Swapping providers = changing one config value.

### Python interface design

```python
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

@dataclass
class LoadsheddingStage:
    stage: int                          # 0-8
    source: str                         # which provider returned this
    retrieved_at: datetime

@dataclass  
class LoadsheddingEvent:
    start: datetime
    end: datetime
    stage: int
    note: Optional[str] = None

@dataclass
class AreaSchedule:
    area_name: str
    area_id: str
    events: list[LoadsheddingEvent]     # upcoming outages
    retrieved_at: datetime
    source: str


class LoadsheddingProvider(ABC):
    """Abstract base — swap implementations without touching business logic."""

    @abstractmethod
    def get_current_stage(self) -> LoadsheddingStage:
        """Return the current national loadshedding stage."""
        ...

    @abstractmethod
    def get_area_schedule(self, area_id: str) -> AreaSchedule:
        """Return upcoming outage events for a specific area."""
        ...

    @abstractmethod
    def search_areas(self, query: str) -> list[dict]:
        """Find area IDs matching a search string."""
        ...


class ESPProvider(LoadsheddingProvider):
    """EskomSePush API implementation."""
    BASE_URL = "https://developer.sepush.co.za/business/2.0"

    def __init__(self, api_key: str):
        self.api_key = api_key
        self._headers = {"token": api_key}

    def get_current_stage(self) -> LoadsheddingStage:
        resp = requests.get(f"{self.BASE_URL}/status", headers=self._headers)
        data = resp.json()
        stage = int(data["status"]["eskom"]["stage"])
        return LoadsheddingStage(stage=stage, source="esp", retrieved_at=datetime.now())

    def get_area_schedule(self, area_id: str) -> AreaSchedule:
        resp = requests.get(f"{self.BASE_URL}/area", headers=self._headers,
                            params={"id": area_id})
        data = resp.json()
        events = [
            LoadsheddingEvent(
                start=datetime.fromisoformat(e["start"]),
                end=datetime.fromisoformat(e["end"]),
                stage=int(e["note"].split()[-1]) if "Stage" in e.get("note","") else 0,
                note=e.get("note")
            ) for e in data.get("events", [])
        ]
        return AreaSchedule(
            area_name=data["info"]["name"],
            area_id=area_id,
            events=events,
            retrieved_at=datetime.now(),
            source="esp"
        )

    def search_areas(self, query: str) -> list[dict]:
        resp = requests.get(f"{self.BASE_URL}/areas_search", headers=self._headers,
                            params={"text": query})
        return resp.json().get("areas", [])


class EskomCalendarProvider(LoadsheddingProvider):
    """Fallback: beyarkay/eskom-calendar CSV."""
    CSV_URL = "https://github.com/beyarkay/eskom-calendar/releases/download/latest/machine_friendly.csv"

    def get_current_stage(self) -> LoadsheddingStage:
        # CSV doesn't carry current stage — return 0 as best guess when no future events
        return LoadsheddingStage(stage=0, source="eskom-calendar", retrieved_at=datetime.now())

    def get_area_schedule(self, area_id: str) -> AreaSchedule:
        df = pd.read_csv(self.CSV_URL, parse_dates=['start', 'finsh'])
        area_df = df[df['area_name'] == area_id]
        events = [
            LoadsheddingEvent(start=row.start, end=row.finsh, stage=int(row.stage))
            for _, row in area_df.iterrows()
        ]
        return AreaSchedule(area_name=area_id, area_id=area_id, events=events,
                            retrieved_at=datetime.now(), source="eskom-calendar")

    def search_areas(self, query: str) -> list[dict]:
        df = pd.read_csv(self.CSV_URL)
        matches = df[df['area_name'].str.contains(query, case=False)]['area_name'].unique()
        return [{"id": m, "name": m} for m in matches]
```

### Caching layer (important for quota management)

```python
import functools
from datetime import timedelta

class CachedLoadsheddingProvider(LoadsheddingProvider):
    """Wraps any provider with TTL caching to protect quota."""

    def __init__(self, provider: LoadsheddingProvider,
                 stage_ttl: timedelta = timedelta(minutes=30),
                 schedule_ttl: timedelta = timedelta(hours=1)):
        self.provider = provider
        self.stage_ttl = stage_ttl
        self.schedule_ttl = schedule_ttl
        self._stage_cache: Optional[LoadsheddingStage] = None
        self._schedule_cache: dict[str, AreaSchedule] = {}

    def get_current_stage(self) -> LoadsheddingStage:
        if (self._stage_cache is None or
                datetime.now() - self._stage_cache.retrieved_at > self.stage_ttl):
            self._stage_cache = self.provider.get_current_stage()
        return self._stage_cache

    def get_area_schedule(self, area_id: str) -> AreaSchedule:
        cached = self._schedule_cache.get(area_id)
        if (cached is None or
                datetime.now() - cached.retrieved_at > self.schedule_ttl):
            self._schedule_cache[area_id] = self.provider.get_area_schedule(area_id)
        return self._schedule_cache[area_id]

    def search_areas(self, query: str) -> list[dict]:
        return self.provider.search_areas(query)
```

### Wiring it up in WattCast config

```python
# config.py
LOADSHEDDING_PROVIDER = "eskom-calendar"   # default — free. Swap to "esp" if subscribing ($55/yr)
ESP_API_KEY = os.getenv("ESP_API_KEY")
REITZ_AREA_ID = "eskde-10-reitzvilleresidentie"  # set after one-time search

# factory.py
def get_loadshedding_provider() -> LoadsheddingProvider:
    if LOADSHEDDING_PROVIDER == "esp":
        return CachedLoadsheddingProvider(ESPProvider(ESP_API_KEY))
    elif LOADSHEDDING_PROVIDER == "eskom-calendar":
        return CachedLoadsheddingProvider(EskomCalendarProvider())
    raise ValueError(f"Unknown provider: {LOADSHEDDING_PROVIDER}")
```

---

## Next Steps

1. **Now (Stage 0):** Implement `EskomCalendarProvider` as default — download CSV and confirm Reitz area name (filter for `reitz`)
2. **Implement `ESPProvider`** as ready-to-switch alternative — keep code dormant until needed
3. **Add `EskomDirectProvider`** using `loadshedding.eskom.co.za/LoadShedding/GetSurburbData` — will activate automatically when loadshedding returns (free, no key)
4. **When loadshedding returns:**
   - Try Eskom Direct API first (free)
   - If Eskom re-publishes Excel files, update URLs and use `parse_eskom_excel.py` logic
   - If sustained Stage 2+, evaluate ESP at $55/yr
5. ~~**Reitz block details to confirm:** Whether Block 14 is odd (U) or even (E) type~~ **CONFIRMED: Even (E)** — slots start at 00:00, 02:00, 04:00... No 1-hour offset.
