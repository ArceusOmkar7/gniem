export interface CrisisMetric {
  average_precision: number;
  precision: number;
  recall: number;
  f1: number;
  positive_rate: number;
  samples: number;
}

export interface CrisisOnsetResponse {
  status: string;
  as_of: string;
  horizon_start: string;
  horizon_end: string;
  score_kind: string;
  threshold: number;
  test_metrics: Record<string, CrisisMetric>;
  data: {
    country_code: string;
    country_name: string | null;
    score: number | null;
    alert: boolean;
    status: string;
    event_count: number;
    baseline_daily_events: number;
  }[];
}
