import React, { useMemo, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import {
  ResponsiveContainer,
  BarChart,
  Bar,
  XAxis,
  YAxis,
  Tooltip,
  CartesianGrid,
  LabelList,
} from 'recharts';
import { useStore } from '../../store/useStore';
import { apiService } from '../../services/api';
import { Users } from 'lucide-react';

interface PeopleMentionsChartProps {
  eventRootCodes?: string[] | null;
  geoFilter?: { countryCode: string | null; stateName: string | null; cityName: string | null };
  themeCategory?: string | null;
}

interface CustomTooltipProps {
  active?: boolean;
  payload?: Array<{ value: number }>;
  label?: string;
  isDark?: boolean;
  metricLabel: string;
}

const truncateName = (name: string): string => {
  return name.length > 24 ? `${name.slice(0, 24)}…` : name;
};

const formatLabel = (value: unknown): string => {
  return (Number(value) || 0).toLocaleString();
};

const CustomTooltip = ({ active, payload, label, isDark, metricLabel }: CustomTooltipProps) => {
  if (!active || !payload?.length) return null;
  const entry = payload[0];

  return (
    <div
      className="rounded-lg px-4 py-3 shadow-xl text-xs font-mono space-y-1.5 border"
      style={{
        background: isDark ? 'rgba(10,10,20,0.96)' : 'rgba(255,255,255,0.97)',
        borderColor: isDark ? 'rgba(0,243,255,0.25)' : 'rgba(15,23,42,0.15)',
        color: isDark ? '#e5e7eb' : '#0F172A',
        minWidth: 180,
      }}
    >
      <div className="font-bold tracking-widest uppercase text-[10px] opacity-60 mb-1">{label}</div>
      <div className="flex items-center justify-between gap-4">
        <span style={{ color: '#00f3ff' }}>{metricLabel}</span>
        <span className="font-bold">{(entry?.value ?? 0).toLocaleString()}</span>
      </div>
    </div>
  );
};

export const PeopleMentionsChart: React.FC<PeopleMentionsChartProps> = ({
  eventRootCodes,
  geoFilter,
  themeCategory,
}) => {
  const { dateRange, dateWindowReady, isDarkTheme } = useStore();
  const [activeTab, setActiveTab] = useState<'people' | 'organizations' | 'cities'>('people');

  const tabConfig = useMemo(() => ({
    people: {
      label: 'People',
      metric: 'Mentions',
      accent: '#00f3ff',
      fetcher: () => apiService.getTopPeople(dateRange[0], dateRange[1], eventRootCodes, geoFilter, themeCategory, 10),
      subtitle: 'GKG persons list',
    },
    organizations: {
      label: 'Orgs',
      metric: 'Mentions',
      accent: '#38bdf8',
      fetcher: () => apiService.getTopOrganizations(dateRange[0], dateRange[1], eventRootCodes, geoFilter, themeCategory, 10),
      subtitle: 'GKG organizations list',
    },
    cities: {
      label: 'Cities',
      metric: 'Events',
      accent: '#22c55e',
      fetcher: () => apiService.getTopCities(dateRange[0], dateRange[1], eventRootCodes, geoFilter, themeCategory, 10),
      subtitle: 'Reverse geocoded',
    },
  }), [dateRange, eventRootCodes, geoFilter, themeCategory]);

  const activeConfig = tabConfig[activeTab];

  const { data, isLoading, isError } = useQuery({
    queryKey: ['top-entities', activeTab, dateRange, eventRootCodes, geoFilter, themeCategory],
    queryFn: () => activeConfig.fetcher(),
    enabled: dateWindowReady,
    staleTime: 60_000,
  });

  const chartData = data?.data ?? [];

  return (
    <div className="glass-panel rounded-xl p-5 shadow-lg border-white/5">
      <div className="flex items-center justify-between mb-4">
        <div className="flex items-center gap-2">
          <Users size={14} className="text-cyber-blue" />
          <span className="data-ink text-cyber-blue">Top Entities</span>
        </div>
        <span className="text-[10px] font-mono text-white/50 uppercase tracking-widest">{activeConfig.subtitle}</span>
      </div>

      <div className="flex items-center gap-2 mb-4">
        {(['people', 'organizations', 'cities'] as const).map((tab) => (
          <button
            key={tab}
            onClick={() => setActiveTab(tab)}
            className={
              `text-[9px] font-mono uppercase tracking-widest px-3 py-1 rounded border transition-colors ` +
              (activeTab === tab
                ? 'text-white border-cyber-blue/50 bg-cyber-blue/10'
                : 'text-white/40 border-white/10 hover:border-white/20')
            }
          >
            {tabConfig[tab].label}
          </button>
        ))}
      </div>

      <div className="h-[280px]">
        {isLoading ? (
          <div className="h-full flex items-center justify-center">
            <div className="flex flex-col items-center gap-3">
              <div className="w-8 h-8 border-2 border-t-cyber-blue border-transparent rounded-full animate-spin" />
              <span className="text-[10px] font-mono text-white/30 uppercase tracking-widest animate-pulse">
                Loading top entities…
              </span>
            </div>
          </div>
        ) : isError || !chartData.length ? (
          <div className="h-full flex items-center justify-center text-[11px] font-mono text-white/30 uppercase">
            No entity data available for selected filters
          </div>
        ) : (
          <ResponsiveContainer width="100%" height="100%">
            <BarChart
              layout="vertical"
              data={chartData}
              margin={{ top: 12, right: 48, left: 16, bottom: 12 }}
            >
              <CartesianGrid strokeDasharray="3 3" stroke={isDarkTheme ? 'rgba(255,255,255,0.08)' : 'rgba(15,23,42,0.08)'} vertical={false} />
              <XAxis
                type="number"
                stroke={isDarkTheme ? 'rgba(255,255,255,0.35)' : 'rgba(15,23,42,0.35)'}
                tick={{ fill: isDarkTheme ? '#E5E7EB' : '#0F172A', fontSize: 10, fontFamily: 'monospace' }}
                tickFormatter={formatLabel}
                axisLine={false}
                tickLine={false}
              />
              <YAxis
                type="category"
                dataKey="name"
                width={160}
                stroke={isDarkTheme ? 'rgba(255,255,255,0.35)' : 'rgba(15,23,42,0.35)'}
                tick={{ fill: isDarkTheme ? '#E5E7EB' : '#0F172A', fontSize: 11, fontFamily: 'monospace' }}
                tickFormatter={truncateName}
                axisLine={false}
                tickLine={false}
              />
              <Tooltip content={<CustomTooltip isDark={isDarkTheme} metricLabel={activeConfig.metric} />} cursor={{ fill: isDarkTheme ? 'rgba(255,255,255,0.06)' : 'rgba(15,23,42,0.06)' }} />
              <Bar dataKey="count" fill={activeConfig.accent} radius={[4, 4, 4, 4]}>
                <LabelList dataKey="count" position="right" formatter={formatLabel} style={{ fill: isDarkTheme ? '#E5E7EB' : '#0F172A', fontSize: 10, fontFamily: 'monospace' }} />
              </Bar>
            </BarChart>
          </ResponsiveContainer>
        )}
      </div>
    </div>
  );
};

export default PeopleMentionsChart;
