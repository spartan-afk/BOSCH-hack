import { useState, useEffect, useCallback } from 'react';
import {
  LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip,
  ResponsiveContainer, ReferenceLine
} from 'recharts';

const API = 'http://localhost:8000/api';

interface MetricPoint { time: number; successRate: number; latency: number; }

interface Props {
  activeRunId: string | null;
  onRunComplete: (id: string) => void;
}

export function LiveRun({ activeRunId, onRunComplete }: Props) {
  const [data, setData] = useState<MetricPoint[]>([]);
  const [status, setStatus] = useState('idle');

  const handleComplete = useCallback(onRunComplete, [onRunComplete]);

  useEffect(() => {
    if (!activeRunId) { setData([]); setStatus('idle'); return; }

    setData([]);
    setStatus('connecting');

    const es = new EventSource(`${API}/experiments/${activeRunId}/stream`);

    es.addEventListener('status', (e: MessageEvent) => {
      try {
        const parsed = JSON.parse(e.data);
        setStatus(parsed.status ?? 'unknown');
        if (['analyzed', 'aborted', 'killed', 'failed'].includes(parsed.status)) {
          es.close();
          setTimeout(() => handleComplete(activeRunId), 1500);
        }
      } catch { /* ignore malformed */ }
    });

    es.addEventListener('metrics', (e: MessageEvent) => {
      try {
        const parsed = JSON.parse(e.data);
        setData(prev => [...prev, {
          time: parsed.elapsed_s ?? 0,
          successRate: (parsed.success_rate ?? 1) * 100,
          latency: parsed.p95_latency_ms ?? 0,
        }].slice(-60));
      } catch { /* ignore */ }
    });

    es.onerror = () => setStatus('stream-error');

    return () => es.close();
  }, [activeRunId, handleComplete]);

  const statusColor: Record<string, string> = {
    idle: 'bg-gray-700 text-gray-400',
    baselining: 'bg-blue-900 text-blue-300',
    injecting: 'bg-orange-900 text-orange-300',
    observing: 'bg-yellow-900 text-yellow-300',
    recovering: 'bg-purple-900 text-purple-300',
    analyzed: 'bg-green-900 text-green-300',
    aborted: 'bg-red-900 text-red-300',
    killed: 'bg-red-900 text-red-300',
    failed: 'bg-red-900 text-red-300',
  };

  if (!activeRunId) {
    return (
      <div className="h-48 flex flex-col items-center justify-center bg-gray-900/60 rounded-xl border border-gray-700 border-dashed">
        <div className="text-3xl mb-2">⚡</div>
        <p className="text-gray-500 text-sm">No experiment running — pick one from the planner</p>
      </div>
    );
  }

  return (
    <div className="bg-gray-900 rounded-xl border border-gray-700 p-4">
      <div className="flex justify-between items-center mb-4">
        <div>
          <p className="text-xs text-gray-500 uppercase tracking-wider mb-1">Live Experiment</p>
          <p className="font-mono text-sm text-gray-300">{activeRunId}</p>
        </div>
        <span className={`px-3 py-1 rounded-full text-xs font-bold uppercase tracking-wider ${statusColor[status] ?? 'bg-gray-700 text-gray-400'}`}>
          {status}
        </span>
      </div>

      <div className="h-56">
        <ResponsiveContainer width="100%" height="100%">
          <LineChart data={data} margin={{ top: 4, right: 16, bottom: 4, left: 0 }}>
            <CartesianGrid strokeDasharray="3 3" stroke="#1f2937" />
            <XAxis dataKey="time" stroke="#4b5563" tickFormatter={v => `${v}s`} tick={{ fontSize: 10 }} />
            <YAxis yAxisId="sr" stroke="#10b981" domain={[0, 100]} tick={{ fontSize: 10 }} tickFormatter={v => `${v}%`} />
            <YAxis yAxisId="lat" orientation="right" stroke="#f59e0b" tick={{ fontSize: 10 }} tickFormatter={v => `${v}ms`} />
            <Tooltip
              contentStyle={{ backgroundColor: '#111827', border: '1px solid #374151', borderRadius: 8 }}
              labelFormatter={v => `t=${v}s`}
            />
            {/* Safety thresholds */}
            <ReferenceLine yAxisId="sr" y={95} stroke="#fbbf24" strokeDasharray="4 2" label={{ value: '95%', fill: '#fbbf24', fontSize: 9 }} />
            <ReferenceLine yAxisId="sr" y={90} stroke="#ef4444" strokeDasharray="4 2" label={{ value: 'abort', fill: '#ef4444', fontSize: 9 }} />
            <Line yAxisId="sr" type="monotone" dataKey="successRate" stroke="#10b981" strokeWidth={2} dot={false} name="Success %" />
            <Line yAxisId="lat" type="monotone" dataKey="latency" stroke="#f59e0b" strokeWidth={2} dot={false} name="p95 ms" />
          </LineChart>
        </ResponsiveContainer>
      </div>
    </div>
  );
}
