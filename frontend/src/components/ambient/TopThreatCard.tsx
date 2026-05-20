/**
 * FILE: frontend/src/components/ambient/TopThreatCard.tsx
 *
 * Collapsible glass-panel card shown in the left sidebar (below Mission Parameters).
 * Fetches from /events/top-threat-countries every 2 min.
 * Each row shows country code + colored 0-100 score bar + numeric score.
 * Clicking a row calls setSelectedCountry() to open the Regional Dossier.
 */

import { useEffect } from 'react';
import { useQuery } from '@tanstack/react-query';
import { ChevronDown, ChevronUp, ShieldAlert, TrendingUp, TrendingDown, AlertCircle } from 'lucide-react';

import { apiService } from '../../services/api';
import { useStore } from '../../store/useStore';
import type { ThreatCountryEntry, CountryDelta, AnomalyEntry } from '../../types';

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function getThreatLabel(score: number): string {
  if (score > 70) return 'CRITICAL';
  if (score > 50) return 'ELEVATED';
  if (score > 30) return 'MODERATE';
  return 'LOW';
}

function getThreatColor(score: number): string {
  if (score > 70) return 'text-cyber-red';
  if (score > 50) return 'text-orange-500';
  if (score > 30) return 'text-amber-400';
  return 'text-terminal-green';
}

function getDeltaColor(delta: number): string {
  // For threat score, lower is better (improvement)
  if (delta < 0) return 'text-terminal-green';
  if (delta > 0) return 'text-cyber-red';
  return 'text-white/20';
}

function scoreColor(score: number): string {
  if (score > 70) return 'text-cyber-red';
  if (score > 50) return 'text-orange-500';
  if (score > 30) return 'text-amber-400';
  return 'text-terminal-green';
}

function barColor(score: number): string {
  if (score > 70) return 'bg-cyber-red';
  if (score > 50) return 'bg-orange-500';
  if (score > 30) return 'bg-amber-400';
  return 'bg-terminal-green';
}

function barGlow(score: number): string {
  if (score > 70) return 'shadow-[0_0_6px_rgba(255,0,60,0.6)]';
  if (score > 50) return 'shadow-[0_0_6px_rgba(249,115,22,0.4)]';
  if (score > 30) return 'shadow-[0_0_6px_rgba(251,191,36,0.4)]';
  return 'shadow-[0_0_6px_rgba(0,255,65,0.4)]';
}

// ---------------------------------------------------------------------------
// Sub-components
// ---------------------------------------------------------------------------

const ThreatRow = ({
  entry,
  rank,
  delta,
  anomaly,
  onClick,
}: {
  entry: ThreatCountryEntry;
  rank: number;
  delta?: CountryDelta;
  anomaly?: AnomalyEntry;
  onClick: () => void;
}) => {
  const pct = Math.min(100, Math.max(0, entry.score));
  return (
    <button
      onClick={onClick}
      className="
        w-full group text-left
        p-2 rounded
        bg-surface-900/40 hover:bg-surface-700/60
        border border-white/5 hover:border-white/15
        transition-all duration-150
      "
    >
      <div className="flex items-center gap-2 mb-1.5">
        {/* Rank badge */}
        <span className="text-[9px] font-mono text-white/25 w-4 shrink-0">
          #{rank}
        </span>
        {/* Country code / name */}
        <div className="flex flex-col flex-1 truncate">
          <div className="flex items-center gap-2">
            <span className="text-[11px] font-mono font-bold text-white/80 group-hover:text-white transition-colors truncate">
              {entry.country_display || entry.country_code}
            </span>
            {anomaly?.is_anomaly && (
              <span className="flex items-center gap-0.5 text-[7px] font-mono bg-amber-500/20 text-amber-400 px-1 rounded border border-amber-500/30 uppercase tracking-tighter">
                <AlertCircle size={7} /> ANOMALY
              </span>
            )}
          </div>
        </div>
        
        {/* Delta */}
        {delta && delta.score_delta !== 0 && (
          <div className={`flex items-center gap-0.5 text-[9px] font-mono ${getDeltaColor(delta.score_delta)} shrink-0`}>
            {delta.score_delta > 0 ? <TrendingUp size={9} /> : <TrendingDown size={9} />}
            {Math.abs(delta.score_delta)}
          </div>
        )}
        {/* Score number */}
        <div className="flex flex-col items-end shrink-0">
          <span className={`text-[11px] font-mono font-bold ${scoreColor(entry.score)}`}>
            {entry.score}
          </span>
          <span className={`text-[7px] font-mono font-bold ${getThreatColor(entry.score)}`}>
            {getThreatLabel(entry.score)}
          </span>
        </div>
      </div>

      {/* Score bar */}
      <div className="ml-6 h-1 bg-white/10 rounded-full overflow-hidden">
        <div
          className={`h-full rounded-full transition-all duration-500 ${barColor(entry.score)} ${barGlow(entry.score)}`}
          style={{ width: `${pct}%` }}
        />
      </div>

      {/* Sub-metrics */}
      <div className="ml-6 mt-1 flex gap-3">
        <span className="text-[9px] font-mono text-white/30">
          {(entry.conflict_ratio * 100).toFixed(0)}% conflict
        </span>
        <span className="text-[9px] font-mono text-white/25">
          Goldstein {entry.avg_goldstein != null ? entry.avg_goldstein.toFixed(2) : '--'}
        </span>
        <span className="text-[9px] font-mono text-white/20">
          {entry.total_events.toLocaleString()} events
        </span>
      </div>
    </button>
  );
};

// ---------------------------------------------------------------------------
// Main component
// ---------------------------------------------------------------------------

export const TopThreatCard = () => {
  const {
    threatCardCollapsed,
    setThreatCardCollapsed,
    setSelectedEvent,
    setSelectedCountry,
    selectedCountry,
    setTopThreats,
    dateRange,
    dateWindowReady,
    geoFilter,
  } = useStore();

  const threatQuery = useQuery({
    queryKey: ['top-threat-countries', dateRange[0], dateRange[1], geoFilter],
    queryFn: () =>
      apiService.getTopThreatCountries(5, dateRange[0], dateRange[1], geoFilter),
    enabled: dateWindowReady,
    staleTime: 120_000,
    refetchInterval: 120_000,
    retry: 1,
  });

  const deltasQuery = useQuery({
    queryKey: ['analytics-deltas'],
    queryFn: () => apiService.getDeltas(),
    staleTime: 3600_000,
    refetchInterval: 3600_000,
  });

  const anomalyQuery = useQuery({
    queryKey: ['anomalies'],
    queryFn: () => apiService.getAnomalies(),
    refetchInterval: 300_000,
  });

  const anomalies = anomalyQuery.data?.data || {};

  useEffect(() => {
    if (threatQuery.data?.data) {
      setTopThreats(threatQuery.data.data);
    }
  }, [threatQuery.data?.data, setTopThreats]);

  const handleRowClick = (countryCode: string) => {
    // Critical ordering rule from CONTEXT.md §7:
    // setSelectedEvent(null) must come BEFORE setSelectedCountry()
    setSelectedEvent(null);
    setSelectedCountry(countryCode);
  };

  return (
    <div className="glass-panel w-full h-full overflow-hidden flex flex-col">
      {/* Header */}
      <button
        onClick={() => setThreatCardCollapsed(!threatCardCollapsed)}
        className="
          w-full flex items-center justify-between
          px-4 py-3
          border-b border-white/5
          hover:bg-white/5 transition-colors
          shrink-0
        "
      >
        <div className="flex items-center gap-2">
          <ShieldAlert size={13} className="text-cyber-red" />
          <span className="data-ink">Threat Monitor</span>
        </div>
        <div className="flex items-center gap-2">
          {!threatCardCollapsed && (
            <span className="text-[9px] font-mono text-white/20 uppercase">
              7d
            </span>
          )}
          {threatCardCollapsed ? (
            <ChevronDown size={13} className="text-white/30" />
          ) : (
            <ChevronUp size={13} className="text-white/30" />
          )}
        </div>
      </button>

      {/* Body */}
      {!threatCardCollapsed && (
        <div className="p-3 space-y-1.5 flex-1 overflow-y-auto custom-scrollbar">
          {threatQuery.isLoading ? (
            <div className="space-y-2">
              {[...Array(5)].map((_, i) => (
                <div
                  key={i}
                  className="h-10 bg-white/5 rounded animate-pulse"
                />
              ))}
            </div>
          ) : threatQuery.isError ? (
            <div className="text-[10px] font-mono text-cyber-red/70 uppercase py-2">
              Signal lost — threat data unavailable
            </div>
          ) : threatQuery.data?.data && threatQuery.data.data.length > 0 ? (
            <>
              {threatQuery.data.data.map((entry, i) => (
                <ThreatRow
                  key={entry.country_code}
                  entry={entry}
                  rank={i + 1}
                  delta={deltasQuery.data?.data[entry.country_code]}
                  anomaly={anomalies[entry.country_code]}
                  onClick={() => handleRowClick(entry.country_code)}
                />
              ))}
              {!selectedCountry && (
                <div className="pt-1 text-[9px] font-mono text-white/15 uppercase tracking-widest text-center animate-pulse">
                  Click to open regional dossier
                </div>
              )}
            </>
          ) : (
            <div className="text-[10px] font-mono text-white/30 uppercase py-2">
              No data in range
            </div>
          )}
        </div>
      )}
    </div>
  );
};
