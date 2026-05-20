import React, { useMemo, useState } from 'react';
import { useQuery, useQueries } from '@tanstack/react-query';
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
import { Users, ExternalLink } from 'lucide-react';

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
  return name.length > 20 ? `${name.slice(0, 20)}…` : name;
};

const formatLabel = (value: unknown): string => {
  return (Number(value) || 0).toLocaleString();
};

const toWikiPageName = (name: string): string => {
  return name
    .toLowerCase()
    .split(' ')
    .map((word) => word.charAt(0).toUpperCase() + word.slice(1))
    .join('_');
};

const CustomYAxisTick = (props: any) => {
  const { x, y, payload, wikiData, isDark } = props;
  const name = payload.value;
  const wiki = wikiData?.[name];

  return (
    <g transform={`translate(${x},${y})`}>
      <foreignObject x={-180} y={-16} width={180} height={32}>
        <div className="flex items-center gap-2 justify-end pr-4 h-full">
          <div className="shrink-0 flex items-center justify-center">
            {wiki?.thumbnail?.source ? (
              <a
                href={wiki.content_urls?.desktop?.page}
                target="_blank"
                rel="noopener noreferrer"
                className="block group/wiki"
                onClick={(e) => e.stopPropagation()}
                title={`View ${name} on Wikipedia`}
              >
                <img
                  src={wiki.thumbnail.source}
                  alt={name}
                  className="w-7 h-7 rounded-full object-cover border border-white/10 group-hover/wiki:border-cyber-blue transition-all shadow-sm"
                />
              </a>
            ) : (
              <div className="w-7 h-7 rounded-full bg-surface-800 border border-white/5 flex items-center justify-center transition-colors">
                 <Users size={12} className="text-white/10" />
              </div>
            )}
          </div>
          <span
            className={`text-[11px] font-mono truncate max-w-[120px] font-bold tracking-tight transition-colors ${
              isDark ? 'text-white/80 hover:text-white' : 'text-surface-900/80 hover:text-black'
            }`}
          >
            {truncateName(name)}
          </span>
        </div>
      </foreignObject>
    </g>
  );
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

  const tabConfig = useMemo(
    () => ({
      people: {
        label: 'People',
        metric: 'Mentions',
        accent: '#00f3ff',
        fetcher: () =>
          apiService.getTopPeople(dateRange[0], dateRange[1], eventRootCodes, geoFilter, themeCategory, 5),
        subtitle: 'GKG persons list',
      },
      organizations: {
        label: 'Orgs',
        metric: 'Mentions',
        accent: '#38bdf8',
        fetcher: () =>
          apiService.getTopOrganizations(
            dateRange[0],
            dateRange[1],
            eventRootCodes,
            geoFilter,
            themeCategory,
            5
          ),
        subtitle: 'GKG organizations list',
      },
      cities: {
        label: 'Cities',
        metric: 'Events',
        accent: '#22c55e',
        fetcher: () =>
          apiService.getTopCities(dateRange[0], dateRange[1], eventRootCodes, geoFilter, themeCategory, 5),
        subtitle: 'Reverse geocoded',
      },
    }),
    [dateRange, eventRootCodes, geoFilter, themeCategory]
  );

  const activeConfig = tabConfig[activeTab];

  const { data, isLoading, isError } = useQuery({
    queryKey: ['top-entities', activeTab, dateRange, eventRootCodes, geoFilter, themeCategory],
    queryFn: () => activeConfig.fetcher(),
    enabled: dateWindowReady,
    staleTime: 60_000,
  });

  const chartData = data?.data ?? [];

  // Fetch Wikipedia summaries for the top entities
  const wikiQueries = useQueries({
    queries: chartData.map((item) => ({
      queryKey: ['wiki-summary', item.name],
      queryFn: async () => {
        const pageName = toWikiPageName(item.name);
        const res = await fetch(`https://en.wikipedia.org/api/rest_v1/page/summary/${pageName}`);
        if (!res.ok) return null;
        return res.json();
      },
      staleTime: 1000 * 60 * 60 * 24, // 1 hour
    })),
  });

  const wikiDataMap = useMemo(() => {
    const map: Record<string, any> = {};
    chartData.forEach((item, index) => {
      if (wikiQueries[index]?.data) {
        map[item.name] = wikiQueries[index].data;
      }
    });
    return map;
  }, [chartData, wikiQueries]);

  return (
    <div className="glass-panel rounded-xl p-5 shadow-lg border-white/5 h-full flex flex-col">
      <div className="flex items-center justify-between mb-4">
        <div className="flex items-center gap-2">
          <Users size={14} className="text-cyber-blue" />
          <span className="data-ink text-cyber-blue">Top Entities</span>
        </div>
        <span className="text-[10px] font-mono text-white/50 uppercase tracking-widest">
          {activeConfig.subtitle}
        </span>
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

      <div className="flex-1 min-h-0">
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
              margin={{ top: 40, right: 48, left: 16, bottom: 12 }}
            >
              <CartesianGrid
                strokeDasharray="3 3"
                stroke={isDarkTheme ? 'rgba(255,255,255,0.08)' : 'rgba(15,23,42,0.08)'}
                vertical={false}
              />
              <XAxis
                type="number"
                stroke={isDarkTheme ? 'rgba(255,255,255,0.35)' : 'rgba(15,23,42,0.35)'}
                tick={{
                  fill: isDarkTheme ? '#E5E7EB' : '#0F172A',
                  fontSize: 10,
                  fontFamily: 'monospace',
                }}
                tickFormatter={formatLabel}
                axisLine={false}
                tickLine={false}
              />
              <YAxis
                type="category"
                dataKey="name"
                width={180}
                stroke={isDarkTheme ? 'rgba(255,255,255,0.35)' : 'rgba(15,23,42,0.35)'}
                tick={<CustomYAxisTick wikiData={wikiDataMap} isDark={isDarkTheme} />}
                axisLine={false}
                tickLine={false}
                interval={0}
              />
              <Tooltip
                content={<CustomTooltip isDark={isDarkTheme} metricLabel={activeConfig.metric} />}
                cursor={{ fill: isDarkTheme ? 'rgba(255,255,255,0.06)' : 'rgba(15,23,42,0.06)' }}
              />
              <Bar dataKey="count" fill={activeConfig.accent} radius={[0, 4, 4, 0]} barSize={24}>
                <LabelList
                  dataKey="count"
                  position="right"
                  formatter={formatLabel}
                  style={{
                    fill: isDarkTheme ? '#E5E7EB' : '#0F172A',
                    fontSize: 10,
                    fontFamily: 'monospace',
                  }}
                />
              </Bar>
            </BarChart>
          </ResponsiveContainer>
        )}
      </div>
    </div>
  );
};

export default PeopleMentionsChart;
