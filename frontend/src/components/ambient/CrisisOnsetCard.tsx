import { useQuery } from '@tanstack/react-query';
import { Activity } from 'lucide-react';
import { apiService } from '../../services/api';
import './crisis-onset.css';

export function CrisisOnsetCard() {
  const { data, isPending, isError, refetch } = useQuery({
    queryKey: ['crisis-onset'], queryFn: apiService.getCrisisOnset,
    staleTime: 60_000, refetchInterval: 300_000, retry: false,
  });
  return (
    <section aria-labelledby="crisis-onset-title" className="glass-panel p-5 rounded-xl border-white/5">
      <div className="flex flex-wrap items-center gap-3 mb-3">
        <Activity size={16} className="text-cyber-blue" aria-hidden="true" />
        <h2 id="crisis-onset-title" className="font-bold text-base">3-day spike-onset outlook</h2>
        <span className="text-xs crisis-note">Experimental TCN</span>
      </div>
      <p className="text-sm crisis-muted max-w-[72ch] mb-4">
        Forecasts unusual news activity. Scores guide review and are uncalibrated;
        they do not measure the probability of a real-world crisis.
      </p>
      {isPending && <p role="status" className="text-sm crisis-muted">Loading the latest completed-day forecast…</p>}
      {isError && <div role="status" className="text-sm crisis-muted">
        Forecasts are unavailable or awaiting a daily refresh.
        <button type="button" onClick={() => void refetch()} className="ml-3 text-cyber-blue underline underline-offset-4 focus-visible:outline focus-visible:outline-2">Retry</button>
      </div>}
      {data && <>
        <p className="text-xs crisis-muted mb-4">As of {data.as_of} UTC · Outlook {data.horizon_start} to {data.horizon_end}</p>
        <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
          <div>
            <div className="flex justify-between text-xs crisis-muted pb-2 border-b border-white/10"><span>Country / region</span><span>Model score</span></div>
            {data.data.filter(row => row.status === 'eligible' && row.score !== null).map(row => (
              <div key={row.country_code} className="flex items-center justify-between gap-4 py-3 border-b border-white/5 text-sm">
                <span className="min-w-0">{row.country_name || row.country_code} <span className="crisis-muted">({row.country_code})</span></span>
                <span className="shrink-0 font-mono tabular-nums">{row.score?.toFixed(3)}{row.alert && <span className="crisis-note ml-2 text-xs">Review</span>}</span>
              </div>
            ))}
            {!data.data.some(row => row.status === 'eligible') && <p className="py-4 text-sm crisis-muted">No eligible regions in this forecast.</p>}
          </div>
          <div className="overflow-x-auto">
            <h3 className="text-sm font-semibold mb-3">Chronological held-out evaluation</h3>
            <table className="crisis-onset-table text-xs w-full text-left">
              <thead className="crisis-muted"><tr><th className="pb-2 pr-3">Model</th><th className="pb-2 pr-3">AP</th><th className="pb-2 pr-3">Precision</th><th className="pb-2">Recall</th></tr></thead>
              <tbody>{[['tcn_focal', 'TCN + focal loss'], ['logistic_regression', 'Logistic baseline'], ['activity_ratio', 'Activity-ratio baseline']].map(([key, label]) => {
                const metric = data.test_metrics[key];
                return metric ? <tr key={key} className="border-t border-white/5"><th scope="row" className="py-3 pr-3 font-normal">{label}</th><td className="font-mono tabular-nums">{metric.average_precision.toFixed(3)}</td><td className="font-mono tabular-nums">{metric.precision.toFixed(3)}</td><td className="font-mono tabular-nums">{metric.recall.toFixed(3)}</td></tr> : null;
              })}</tbody>
            </table>
            <p className="text-xs crisis-muted mt-3">AP is non-interpolated precision-recall area. Thresholds were selected on validation data. A spike means at least 2× the prior 7-day mean and 10 events.</p>
          </div>
        </div>
      </>}
    </section>
  );
}
