export interface Event {
  global_event_id: number;
  sql_date: string;
  lat: number;
  lon: number;
  action_geo_country_code?: string;
  action_geo_lat?: number;
  action_geo_long?: number;
  actor1_country_code?: string;
  actor2_country_code?: string;
  quad_class?: number;
  actor1_type_code?: string;
  actor2_type_code?: string;
  event_code?: string;
  actor1_geo_country_code?: string;
  actor2_geo_country_code?: string;
  event_root_code?: string;
  goldstein_scale?: number;
  num_mentions: number;
  num_sources?: number;
  avg_tone?: number;
  source_url?: string;
  actor1_type?: string;
  actor2_type?: string;
  themes?: string[];
  persons?: string[];
  organizations?: string[];
  mentions_count?: number;
  avg_confidence?: number;
}

export interface MapAggregation {
  lat: number;
  lon: number;
  intensity: number;
  country_code?: string;
}

export interface MapDataResponse {
  zoom: number;
  is_aggregated: boolean;
  count: number;
  data: MapAggregation[] | Event[];
}

export interface EntityGroup {
  countries: string[];
  organizations: string[];
  persons: string[];
}

export interface EventAnalysis {
  summary: string;
  sentiment: 'Positive' | 'Neutral' | 'Negative';
  entities: EntityGroup;
  themes: string[];
  confidence: number;
  images: string[];
  embeds: string[];
}

export interface ViewState {
  longitude: number;
  latitude: number;
  zoom: number;
  pitch: number;
  bearing: number;
}

export interface BigQueryHealthDetail {
  connected: boolean;
  project: string;
  dataset: string;
  latency_ms?: number | null;
  error?: string | null;
}

export interface HotTierHealthDetail {
  path: string;
  available: boolean;
  parquet_files: number;
  cutoff_days: number;
  coverage_days: number;
  total_rows?: number;
  last_updated_at?: string | null;
}

export interface HealthResponse {
  status: 'healthy' | 'degraded' | 'unhealthy' | string;
  environment: string;
  version: string;
  bigquery: BigQueryHealthDetail;
  hot_tier: HotTierHealthDetail;
  uptime_seconds: number;
}

export interface RuntimeSettingsResponse {
  hot_tier_cutoff_days: number;
  cold_tier_max_window_days: number;
  cold_tier_monthly_query_limit: number;
  bq_max_scan_bytes: number;
  default_lookback_days: number;
  default_query_limit: number;
  realtime_fetch_interval_minutes: number;
  daily_batch_cron_utc: string;
  nightly_ai_cron_utc: string;
}

export interface RiskScoreResponse {
  score: number;
  trend: string;
  country_display?: string;
  conflict_ratio: number;
  avg_goldstein: number | null;
  avg_tone: number | null;
  total_events: number;
}

export interface CountryDelta {
  event_delta_pct: number;
  conflict_delta: number;
  tone_delta: number;
  score_delta: number;
}

export interface AnalyticsDeltaResponse {
  data: Record<string, CountryDelta>;
}

export interface ForecastPoint {
  date: string;
  predicted_count: number;
  lower_bound: number | null;
  upper_bound: number | null;
}

export interface ForecastResponse {
  country_code: string | null;
  horizon_days: number;
  model_type: string;
  historical_summary: Record<string, unknown>;
  predictions: ForecastPoint[];
}

// ---------------------------------------------------------------------------
// 15.1 — Global Pulse
// ---------------------------------------------------------------------------
 
export interface GlobalPulseResponse {
  total_events_today: number;
  most_active_country: string | null;
  most_active_display?: string | null;
  most_active_count: number;
  most_hostile_country: string | null;
  most_hostile_display?: string | null;
  avg_global_tone: number | null;
  global_conflict_ratio: number;
}

export interface EntityCountResponse {
  name: string;
  count: number;
}

export interface EntityCountListResponse {
  count: number;
  data: EntityCountResponse[];
}
 
// ---------------------------------------------------------------------------
// PHASE 4 — Activity Spikes & Anomalies
// ---------------------------------------------------------------------------

export interface ThreatCountryEntry {
  country_code: string;
  country_name?: string;
  country_display?: string;
  score: number;
  avg_goldstein?: number | null;
  conflict_ratio: number;
  total_events: number;
}

export interface TopThreatCountriesResponse {
  count: number;
  data: ThreatCountryEntry[];
}

export interface SpikeAlertEntry {
  country_code: string;
  country_name?: string;
  country_display?: string;
  events_24h: number;
  baseline_avg: number;
  spike_ratio: number;
  top_cameo_root?: string;
}

export interface SpikeAlertResponse {
  count: number;
  data: SpikeAlertEntry[];
}

export interface AnomalyEntry {
  country_name?: string;
  country_display?: string;
  is_anomaly: boolean;
  score: number;
  reason: string | null;
}

export interface AnomalyResponse {
  data: Record<string, AnomalyEntry>;
}

export interface CountryBriefingEntry {
  briefing: string;
  generated_at: string;
  source: string;
  summary: string;
}

export interface BriefingsResponse {
  count: number;
  data: Record<string, CountryBriefingEntry>;
}

export interface GeoDrillItem {
  name: string;
  count: number;
  code?: string;
  display?: string;
}

export interface GeoDrillResponse {
  level: 'country' | 'state' | 'city';
  items: GeoDrillItem[];
  state_available?: boolean;
  state_reason?: string;
}

export interface ThemeCategoriesResponse {
  data: Record<string, number>;
}

export interface LiveStreamChannel {
  id: string;
  name: string;
  video_id?: string | null;
  embed_url?: string | null;
  can_embed: boolean;
  status: string;
  error?: string | null;
}

export interface LiveStreamGroupResponse {
  group_key: string;
  label: string;
  channels: LiveStreamChannel[];
}
