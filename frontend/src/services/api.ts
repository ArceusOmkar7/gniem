import type {
  HealthResponse,
  MapDataResponse,
  EventAnalysis,
  RuntimeSettingsResponse,
  RiskScoreResponse,
  ForecastResponse,
  GlobalPulseResponse,
  EntityCountListResponse,
  TopThreatCountriesResponse,
  AnalyticsDeltaResponse,
  SpikeAlertResponse,
  AnomalyResponse,
  BriefingsResponse,
  GeoDrillResponse,
  ThemeCategoriesResponse,
  LiveStreamGroupResponse,
  LiveStreamChannel,
} from '../types';

const API_BASE_URL = import.meta.env.VITE_API_URL || 'http://localhost:8000/api/v1';

type GeoFilter = { countryCode: string | null; stateName: string | null; cityName: string | null };

const appendGeoFilters = (
  params: URLSearchParams,
  geoFilter?: GeoFilter,
  includeStateCity: boolean = true
) => {
  if (geoFilter?.countryCode) params.append('geo_country', geoFilter.countryCode);
  if (includeStateCity) {
    if (geoFilter?.stateName) params.append('geo_state', geoFilter.stateName);
    if (geoFilter?.cityName) params.append('geo_city', geoFilter.cityName);
  }
};

export const apiService = {
  getMapData: async (
    bbox: { n: number; s: number; e: number; w: number },
    zoom: number,
    startDate: string,
    endDate: string,
    eventRootCodes?: string[] | null,
    geoFilter?: GeoFilter,
    themeCategory?: string | null,
    signal?: AbortSignal
  ): Promise<MapDataResponse> => {
    const params = new URLSearchParams({
      bbox_n: bbox.n.toString(),
      bbox_s: bbox.s.toString(),
      bbox_e: bbox.e.toString(),
      bbox_w: bbox.w.toString(),
      zoom: zoom.toString(),
      start_date: startDate,
      end_date: endDate,
    });

    if (eventRootCodes?.length) params.append('event_root_codes', eventRootCodes.join(','));
    if (themeCategory) params.append('theme_category', themeCategory);
    appendGeoFilters(params, geoFilter, true);

    const response = await fetch(`${API_BASE_URL}/events/map?${params}`, { signal });
    if (!response.ok) {
      throw new Error(`Failed to fetch map data: ${response.statusText}`);
    }
    return response.json();
  },

  getGlobalEvents: async (
    startDate: string,
    endDate: string,
    eventRootCodes?: string[] | null,
    limit: number = 50,
    geoFilter?: GeoFilter,
    themeCategory?: string | null
  ) => {
    const params = new URLSearchParams({
      start_date: startDate,
      end_date: endDate,
      limit: limit.toString(),
    });
    if (eventRootCodes?.length) params.append('event_root_codes', eventRootCodes.join(','));
    if (themeCategory) params.append('theme_category', themeCategory);
    appendGeoFilters(params, geoFilter);

    const response = await fetch(`${API_BASE_URL}/events?${params}`);
    if (!response.ok) {
      throw new Error(`Failed to fetch global events: ${response.statusText}`);
    }
    return response.json();
  },

  getEventsByRegion: async (
    countryCode: string,
    startDate: string,
    endDate: string,
    limit: number = 10,
    geoFilter?: GeoFilter
  ) => {
    const params = new URLSearchParams({
      start_date: startDate,
      end_date: endDate,
      limit: limit.toString(),
    });
    appendGeoFilters(params, geoFilter);

    const response = await fetch(`${API_BASE_URL}/events/region/${countryCode}?${params}`);
    if (!response.ok) {
      throw new Error(`Failed to fetch regional events: ${response.statusText}`);
    }
    return response.json();
  },

  getRegionalStats: async (
    countryCode: string,
    startDate: string,
    endDate: string,
    geoFilter?: GeoFilter
  ) => {
    const params = new URLSearchParams({
      start_date: startDate,
      end_date: endDate,
    });
    appendGeoFilters(params, geoFilter);

    const response = await fetch(`${API_BASE_URL}/events/region/${countryCode}/stats?${params}`);
    if (!response.ok) {
      throw new Error(`Failed to fetch regional stats: ${response.statusText}`);
    }
    return response.json();
  },

  getRiskScore: async (
    countryCode: string,
    startDate: string,
    endDate: string,
    geoFilter?: GeoFilter
  ): Promise<RiskScoreResponse> => {
    const params = new URLSearchParams({
      start_date: startDate,
      end_date: endDate,
    });
    appendGeoFilters(params, geoFilter);

    const response = await fetch(`${API_BASE_URL}/events/region/${countryCode}/risk-score?${params}`);
    if (!response.ok) {
      throw new Error(`Failed to fetch risk score: ${response.statusText}`);
    }
    return response.json();
  },

  getForecast: async (countryCode: string): Promise<ForecastResponse> => {
    const response = await fetch(`${API_BASE_URL}/analytics/forecast/${countryCode}`);
    if (!response.ok) {
      throw new Error(`Failed to fetch forecast: ${response.statusText}`);
    }
    return response.json();
  },

  analyzeEvent: async (eventId: number): Promise<EventAnalysis> => {
    const response = await fetch(`${API_BASE_URL}/events/${eventId}/analyze`);
    if (!response.ok) {
      throw new Error(`Failed to analyze event: ${response.statusText}`);
    }
    return response.json();
  },

  getHealth: async (): Promise<HealthResponse> => {
    const response = await fetch(`${API_BASE_URL}/health`);
    if (!response.ok) {
      throw new Error(`Failed to fetch health status: ${response.statusText}`);
    }
    return response.json();
  },

  getRuntimeSettings: async (): Promise<RuntimeSettingsResponse> => {
    const response = await fetch(`${API_BASE_URL}/health/settings`);
    if (!response.ok) {
      throw new Error(`Failed to fetch runtime settings: ${response.statusText}`);
    }
    return response.json();
  },
  getGlobalPulse: async (
    startDate: string,
    endDate: string,
    eventRootCodes?: string[] | null,
    geoFilter?: GeoFilter,
    themeCategory?: string | null
  ): Promise<GlobalPulseResponse> => {
    const params = new URLSearchParams({ start_date: startDate, end_date: endDate });
    if (eventRootCodes?.length) params.append('event_root_codes', eventRootCodes.join(','));
    if (themeCategory) params.append('theme_category', themeCategory);
    appendGeoFilters(params, geoFilter);
    const response = await fetch(`${API_BASE_URL}/events/global-pulse?${params}`);
    if (!response.ok) {
      throw new Error(`Failed to fetch global pulse: ${response.statusText}`);
    }
    return response.json();
  },
 
  getTopThreatCountries: async (
    limit: number = 5,
    startDate?: string,
    endDate?: string,
    geoFilter?: GeoFilter
  ): Promise<TopThreatCountriesResponse> => {
    const params = new URLSearchParams({ limit: limit.toString() });
    if (startDate) params.append('start_date', startDate);
    if (endDate) params.append('end_date', endDate);
    appendGeoFilters(params, geoFilter);
    const response = await fetch(`${API_BASE_URL}/events/top-threat-countries?${params}`);
    if (!response.ok) {
      throw new Error(`Failed to fetch top threat countries: ${response.statusText}`);
    }
    return response.json();
  },

  getDeltas: async (): Promise<AnalyticsDeltaResponse> => {
    const response = await fetch(`${API_BASE_URL}/analytics/deltas`);
    if (!response.ok) {
      throw new Error(`Failed to fetch deltas: ${response.statusText}`);
    }
    return response.json();
  },

  getActivitySpikes: async (): Promise<SpikeAlertResponse> => {
    const response = await fetch(`${API_BASE_URL}/analytics/spikes`);
    if (!response.ok) {
      throw new Error(`Failed to fetch activity spikes: ${response.statusText}`);
    }
    return response.json();
  },

  getAnomalies: async (): Promise<AnomalyResponse> => {
    const response = await fetch(`${API_BASE_URL}/analytics/anomalies`);
    if (!response.ok) {
      throw new Error(`Failed to fetch anomalies: ${response.statusText}`);
    }
    return response.json();
  },

  getBriefings: async (): Promise<BriefingsResponse> => {
    const response = await fetch(`${API_BASE_URL}/analytics/briefings`);
    if (!response.ok) {
      throw new Error(`Failed to fetch briefings: ${response.statusText}`);
    }
    return response.json();
  },

  getDailyTrend: async (
    startDate: string,
    endDate: string,
    eventRootCodes?: string[] | null,
    geoFilter?: GeoFilter,
    themeCategory?: string | null
  ): Promise<{ data: { date: string; total: number; conflict: number }[] }> => {
    const params = new URLSearchParams({ start_date: startDate, end_date: endDate });
    if (eventRootCodes?.length) params.append('event_root_codes', eventRootCodes.join(','));
    if (themeCategory) params.append('theme_category', themeCategory);
    appendGeoFilters(params, geoFilter);
    const response = await fetch(`${API_BASE_URL}/events/daily-trend?${params}`);
    if (!response.ok) throw new Error(`Failed to fetch daily trend: ${response.statusText}`);
    return response.json();
  },

  getGeoDrill: async (
    startDate: string,
    endDate: string,
    countryCode?: string | null,
    stateName?: string | null
  ): Promise<GeoDrillResponse> => {
    const params = new URLSearchParams({ start_date: startDate, end_date: endDate });
    if (countryCode) params.append('country_code', countryCode);
    if (stateName) params.append('state_name', stateName);
    const response = await fetch(`${API_BASE_URL}/events/geo-drill?${params}`);
    if (!response.ok) {
      throw new Error(`Failed to fetch geo drill: ${response.statusText}`);
    }
    return response.json();
  },

  getThemeCategories: async (): Promise<ThemeCategoriesResponse> => {
    const response = await fetch(`${API_BASE_URL}/analytics/theme-categories`);
    if (!response.ok) {
      throw new Error(`Failed to fetch theme categories: ${response.statusText}`);
    }
    return response.json();
  },

  getTopPeople: async (
    startDate: string,
    endDate: string,
    eventRootCodes?: string[] | null,
    geoFilter?: GeoFilter,
    themeCategory?: string | null,
    limit: number = 10,
  ): Promise<EntityCountListResponse> => {
    const params = new URLSearchParams({
      start_date: startDate,
      end_date: endDate,
      limit: limit.toString(),
    });
    if (eventRootCodes?.length) params.append('event_root_codes', eventRootCodes.join(','));
    if (themeCategory) params.append('theme_category', themeCategory);
    appendGeoFilters(params, geoFilter);

    const response = await fetch(`${API_BASE_URL}/events/top-people?${params}`);
    if (!response.ok) {
      throw new Error(`Failed to fetch top people: ${response.statusText}`);
    }
    return response.json();
  },

  getTopOrganizations: async (
    startDate: string,
    endDate: string,
    eventRootCodes?: string[] | null,
    geoFilter?: GeoFilter,
    themeCategory?: string | null,
    limit: number = 10,
  ): Promise<EntityCountListResponse> => {
    const params = new URLSearchParams({
      start_date: startDate,
      end_date: endDate,
      limit: limit.toString(),
    });
    if (eventRootCodes?.length) params.append('event_root_codes', eventRootCodes.join(','));
    if (themeCategory) params.append('theme_category', themeCategory);
    appendGeoFilters(params, geoFilter);

    const response = await fetch(`${API_BASE_URL}/events/top-organizations?${params}`);
    if (!response.ok) {
      throw new Error(`Failed to fetch top organizations: ${response.statusText}`);
    }
    return response.json();
  },

  getTopCities: async (
    startDate: string,
    endDate: string,
    eventRootCodes?: string[] | null,
    geoFilter?: GeoFilter,
    themeCategory?: string | null,
    limit: number = 10,
  ): Promise<EntityCountListResponse> => {
    const params = new URLSearchParams({
      start_date: startDate,
      end_date: endDate,
      limit: limit.toString(),
    });
    if (eventRootCodes?.length) params.append('event_root_codes', eventRootCodes.join(','));
    if (themeCategory) params.append('theme_category', themeCategory);
    appendGeoFilters(params, geoFilter);

    const response = await fetch(`${API_BASE_URL}/events/top-cities?${params}`);
    if (!response.ok) {
      throw new Error(`Failed to fetch top cities: ${response.statusText}`);
    }
    return response.json();
  },

  getTopSources: async (
    startDate: string,
    endDate: string,
    eventRootCodes?: string[] | null,
    geoFilter?: GeoFilter,
    themeCategory?: string | null,
    limit: number = 10,
  ): Promise<EntityCountListResponse> => {
    const params = new URLSearchParams({
      start_date: startDate,
      end_date: endDate,
      limit: limit.toString(),
    });
    if (eventRootCodes?.length) params.append('event_root_codes', eventRootCodes.join(','));
    if (themeCategory) params.append('theme_category', themeCategory);
    appendGeoFilters(params, geoFilter);

    const response = await fetch(`${API_BASE_URL}/events/top-sources?${params}`);
    if (!response.ok) {
      throw new Error(`Failed to fetch top sources: ${response.statusText}`);
    }
    return response.json();
  },

  getLiveStreams: async (countryCode?: string | null): Promise<LiveStreamGroupResponse> => {
    const params = new URLSearchParams();
    if (countryCode) params.append('country_code', countryCode);
    const response = await fetch(`${API_BASE_URL}/analytics/live-streams?${params}`);
    if (!response.ok) {
      throw new Error(`Failed to fetch live streams: ${response.statusText}`);
    }
    return response.json();
  },

  refreshLiveStream: async (channelId: string): Promise<LiveStreamChannel> => {
    const params = new URLSearchParams({ channel_id: channelId });
    const response = await fetch(`${API_BASE_URL}/analytics/live-streams/refresh?${params}`);
    if (!response.ok) {
      throw new Error(`Failed to refresh live stream: ${response.statusText}`);
    }
    return response.json();
  },
};
