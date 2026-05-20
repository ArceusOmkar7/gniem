# Dashboard Revamp — Backend Implementation Plan

Focused on **minor** and **moderate** backend changes to support the new scrollable dashboard layout, without introducing new data sources.

---

### Phase 1: Extending Existing Event Endpoints (Minor Changes)

These changes add missing fields and grouping endpoints to data we already have loaded in the hot-tier (DuckDB).

**1. Add Goldstein Average to Threat Ranking**
*   **Goal**: Populate the "Top Regions by Hostility" sidebar with accurate Goldstein averages per country.
*   **Backend Changes**:
    *   **Schema (`backend/api/schemas/schemas.py`)**: Update the existing `ThreatCountryEntry` model to include `avg_goldstein: float = 0.0`.
    *   **Repository (`backend/infrastructure/duckdb_repository.py`)**: Modify the `get_top_threat_countries` SQL query to include `AVG(GoldsteinScale)` in its `SELECT` block and map it to `avg_goldstein`.

**2. Top Organizations and Cities Endpoints**
*   **Goal**: Allow tab-toggling between PEOPLE / ORGS / CITIES in the Top Entities panel.
*   **Backend Changes**:
    *   **Router (`backend/api/routers/events.py`)**: Add two new `GET` endpoints: `/events/top-organizations` and `/events/top-cities`.
    *   **Repository (`backend/infrastructure/duckdb_repository.py`)**: 
        *   For Cities: Query hot-tier grouped by `ActionGeo_FullName` where `ActionGeo_Type >= 3` (City-level or higher).
        *   For Orgs: Query hot-tier using the `organizations` column (extracted from GKG or extrapolated if needed) ordered by count.

**3. Sentiment Overview (QuadClass Distribution)**
*   **Goal**: Support the Donut chart showing Hostile / Neutral / Positive splits.
*   **Backend Changes**:
    *   **Schema (`backend/api/schemas/schemas.py`)**: Add a `SentimentOverview` schema containing percentages for `hostile`, `neutral`, and `positive`.
    *   **Repository (`backend/infrastructure/duckdb_repository.py`)**: Write an aggregated query counting events by `QuadClass`. 
        *   Hostile = QuadClass 3 & 4 (Verbal & Material Conflict).
        *   Neutral = QuadClass 1 (Verbal Cooperation with average Tone near 0).
        *   Positive = QuadClass 2 (Material Cooperation) or QuadClass 1 with high positive Tone.
    *   **Router**: Add this to the existing `/events/global-pulse` endpoint payload so it can be fetched in one request.

---

### Phase 2: Derived Metrics & Indices (Moderate Changes)

These changes involve aggregating existing data to create new composite scores. 

**4. Global Instability Index**
*   **Goal**: Drive the 0-100 "Instability Index" gauge.
*   **Backend Changes**:
    *   **Logic (`backend/application/use_cases/analyze_event.py`)**: Create a calculation that creates a weighted global average utilizing the existing `conflict_ratio`, `avg_goldstein`, and `avg_tone`.
    *   Normalize this down to an integer between 0 and 100.
    *   Append `global_instability: int` to the `/events/global-pulse` response.

**5. AI Confidence Metric**
*   **Goal**: Provide a data confidence score without needing external ML evaluation.
*   **Backend Changes**:
    *   **Repository**: Add `AVG(Confidence) as ai_confidence` to the global pulse query.
    *   Include this metric on the `/events/global-pulse` payload.

**6. Approximate Event Velocity & Sparkline**
*   **Goal**: Drive the "Events / Hr" sparkline without having hourly partitions.
*   **Backend Changes**:
    *   **Router (`backend/api/routers/events.py`)**: Add `GET /events/velocity`.
    *   **Logic**: Query the total events for the current day and divide it by the number of hours elapsed today (Fallback: `total_events / 24`). For the sparkline, return the daily event totals for the last 7 days.

---

### Phase 3: Automated Nightly Jobs (Moderate Changes)

These changes extend our existing nightly AI processing to generate assets needed for the revamped UI.

**7. Global AI Summary Generation**
*   **Goal**: Create a high-level briefing for the top right dashboard panel.
*   **Backend Changes**:
    *   **Script (`scripts/nightly_ai.py`)**: Update this script to read the top 3 country briefings it just generated, and prompt the LLM to summarize them into a single 3-sentence global briefing.
    *   Save this output to `data/cache/global_briefing.json`.
    *   **Router (`backend/api/routers/analytics.py`)**: Expose a new `/analytics/global-briefing` endpoint that reads and returns this JSON file.

**8. Trend Annotations (Callouts)**
*   **Goal**: Provide string labels for the Event Volume Trend Chart (e.g., "Major Escalation").
*   **Backend Changes**:
    *   **Script (`scripts/nightly_ai.py`)**: After the IsolationForest model finishes detecting spikes (anomalies), take the date and region of the highest 3 spikes. Pass these to the LLM to generate a short label.
    *   Save mapping to `data/cache/trend_annotations.json`.
    *   **Router (`backend/api/routers/analytics.py`)**: Expose `/analytics/trend-annotations`.

---

### Phase 4: Frontend UI / UX Guidelines 

*   **Layout Flow (Scrollable)**: Use an `overflow-y-auto` scrolling container.
    *   **Row 1**: Header, Filters, and Date Pickers.
    *   **Row 2**: Scrolling Alert Ticker.
    *   **Row 3**: KPI Cards (Global Events, Active Region, AI Confidence, Instability Index).
    *   **Row 4**: Left (60%): Event Volume Trend Chart | Right (40%): AI Global Summary & Sentiment Donut Chart.
    *   **Row 5**: Left (60%): **Inline Map View** (constrained height with a "Open Globe View" routing button) | Right (40%): Ranked Lists (Hostility / Entities).
*   **Bottom Status Bar**: Update `GlobalStatsTicker.tsx`. Ensure "EVENTS TODAY: [Total]" is prominently displayed at the start. Removed AI Model version.
