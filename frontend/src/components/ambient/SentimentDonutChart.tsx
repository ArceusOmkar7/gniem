import React from 'react';
import { PieChart, Pie, Cell, ResponsiveContainer, Tooltip, Legend } from 'recharts';
import { useStore } from '../../store/useStore';
import { useQuery } from '@tanstack/react-query';
import { apiService } from '../../services/api';
import { Activity } from 'lucide-react';

export const SentimentDonutChart: React.FC = () => {
  const { dateRange, eventRootCodes, geoFilter, dateWindowReady, isDarkTheme } = useStore();

  const { data: pulseData, isLoading } = useQuery({
    queryKey: ['global-pulse', dateRange[0], dateRange[1], eventRootCodes, geoFilter],
    queryFn: () => apiService.getGlobalPulse(dateRange[0], dateRange[1], eventRootCodes, geoFilter),
    enabled: dateWindowReady,
    staleTime: 60000,
  });

  const sentiment = pulseData?.sentiment;

  const chartData = [
    { name: 'Hostile', value: sentiment?.hostile || 0, color: '#ff003c' },
    { name: 'Neutral', value: sentiment?.neutral || 0, color: '#fbbf24' },
    { name: 'Positive', value: sentiment?.positive || 0, color: '#22c55e' },
  ].filter(d => d.value > 0);

  const total = chartData.reduce((acc, curr) => acc + curr.value, 0);

  return (
    <div className="glass-panel p-5 rounded-xl shadow-lg border-white/5 flex flex-col h-full">
      <div className="flex items-center gap-2 mb-4">
        <Activity size={14} className="text-cyber-blue" />
        <span className="data-ink text-cyber-blue text-[10px]">Sentiment Overview</span>
      </div>

      <div className="flex-1 min-h-[200px] relative">
        {isLoading ? (
          <div className="absolute inset-0 flex items-center justify-center">
             <div className="w-8 h-8 border-2 border-t-cyber-blue border-transparent rounded-full animate-spin" />
          </div>
        ) : chartData.length === 0 ? (
          <div className="absolute inset-0 flex items-center justify-center text-[10px] font-mono text-white/30 uppercase">
            No sentiment data
          </div>
        ) : (
          <>
            <ResponsiveContainer width="100%" height="100%">
              <PieChart>
                <Pie
                  data={chartData}
                  cx="50%"
                  cy="50%"
                  innerRadius={55}
                  outerRadius={75}
                  paddingAngle={5}
                  dataKey="value"
                  stroke="none"
                  label={({ name, percent }) => `${(percent * 100).toFixed(0)}%`}
                  labelLine={false}
                >
                  {chartData.map((entry, index) => (
                    <Cell key={`cell-${index}`} fill={entry.color} />
                  ))}
                </Pie>
                <Tooltip
                  contentStyle={{
                    backgroundColor: isDarkTheme ? '#0F172A' : '#FFFFFF',
                    borderColor: 'rgba(0,243,255,0.2)',
                    fontSize: '10px',
                    fontFamily: 'monospace',
                  }}
                  itemStyle={{ color: isDarkTheme ? '#E5E7EB' : '#0F172A' }}
                />
              </PieChart>
            </ResponsiveContainer>
            
            {/* Center text overlay */}
            <div className="absolute inset-0 flex flex-col items-center justify-center pointer-events-none">
              <span className="text-2xl font-bold font-mono text-white">
                {sentiment?.hostile ? Math.round(sentiment.hostile) : 0}%
              </span>
              <span className="text-[8px] font-mono text-white/40 uppercase">Hostile</span>
            </div>
          </>
        )}
      </div>

      <div className="grid grid-cols-3 gap-2 mt-4">
        {chartData.map((d) => (
          <div key={d.name} className="flex flex-col items-center">
            <div className="flex items-center gap-1.5 mb-1">
              <div className="w-1.5 h-1.5 rounded-full" style={{ backgroundColor: d.color }} />
              <span className="text-[8px] font-mono text-white/50 uppercase">{d.name}</span>
            </div>
            <span className="text-xs font-mono font-bold text-white">{d.value.toFixed(1)}%</span>
          </div>
        ))}
      </div>
    </div>
  );
};
