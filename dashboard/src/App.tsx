import { useState, useEffect, useCallback } from 'react';
import { Topology } from './components/Topology';
import { LiveRun } from './components/LiveRun';
import { ErrorBoundary } from './components/ErrorBoundary';

const API = 'http://localhost:8000/api';

// ── types ────────────────────────────────────────────────────────────────────

interface Health {
  success_rate: number;
  p50_latency_ms: number;
  p95_latency_ms: number;
  error_rate: number;
  pods?: { total_pods: number; ready_pods: number };
}

interface PlanCandidate {
  score: number;
  impact: number;
  risk: number;
  rationale: string;
  spec: {
    id: string;
    name: string;
    hypothesis: string;
    type: string;
    target: { service: string; namespace: string };
    duration_s: number;
  };
}

interface Report {
  experiment_id: string;
  verdict: 'passed' | 'degraded' | 'failed';
  interpretation: string;
  weakness?: string;
  recommendations?: string[];
}

// ── helper: safe fetch ───────────────────────────────────────────────────────

async function apiFetch<T>(path: string): Promise<T | null> {
  try {
    const res = await fetch(`${API}${path}`);
    if (!res.ok) return null;
    return await res.json() as T;
  } catch {
    return null;
  }
}

// ── sub-components ────────────────────────────────────────────────────────────

function MetricPill({ label, value, ok }: { label: string; value: string; ok?: boolean }) {
  return (
    <div className="flex flex-col items-end">
      <span className="text-[10px] text-gray-500 uppercase tracking-widest">{label}</span>
      <span className={`font-mono font-bold text-base ${ok === false ? 'text-red-400' : ok === true ? 'text-emerald-400' : 'text-blue-400'}`}>
        {value}
      </span>
    </div>
  );
}

function ScoreBar({ value, color = 'bg-blue-500' }: { value: number; color?: string }) {
  return (
    <div className="w-full h-1 bg-gray-700 rounded mt-1">
      <div className={`h-1 rounded ${color}`} style={{ width: `${Math.min(value * 100, 100)}%` }} />
    </div>
  );
}

function VerdictBadge({ verdict }: { verdict: string }) {
  const map: Record<string, string> = {
    passed: 'bg-emerald-900/60 text-emerald-300 border-emerald-700',
    degraded: 'bg-yellow-900/60 text-yellow-300 border-yellow-700',
    failed: 'bg-red-900/60 text-red-300 border-red-700',
  };
  const icons: Record<string, string> = { passed: '✓', degraded: '⚠', failed: '✗' };
  return (
    <span className={`px-2 py-0.5 rounded border text-xs font-bold uppercase tracking-wider ${map[verdict] ?? 'bg-gray-800 text-gray-400 border-gray-600'}`}>
      {icons[verdict] ?? '?'} {verdict}
    </span>
  );
}

// ── main app ─────────────────────────────────────────────────────────────────

export default function App() {
  const [health, setHealth] = useState<Health | null>(null);
  const [graph, setGraph] = useState<any>(null);
  const [candidates, setCandidates] = useState<PlanCandidate[]>([]);
  const [reports, setReports] = useState<Report[]>([]);
  const [activeRunId, setActiveRunId] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [toast, setToast] = useState<{ msg: string; type: 'error' | 'info' } | null>(null);

  const showToast = (msg: string, type: 'error' | 'info' = 'info') => {
    setToast({ msg, type });
    setTimeout(() => setToast(null), 4000);
  };

  const loadAll = useCallback(async () => {
    const [h, g, p] = await Promise.all([
      apiFetch<Health>('/health'),
      apiFetch<any>('/graph'),
      apiFetch<{ candidates: PlanCandidate[] }>('/plan'),
    ]);
    if (h) setHealth(h);
    if (g) setGraph(g);
    if (p?.candidates) setCandidates(p.candidates);
  }, []);

  useEffect(() => {
    loadAll();
    const id = setInterval(() => apiFetch<Health>('/health').then(h => h && setHealth(h)), 5000);
    return () => clearInterval(id);
  }, [loadAll]);

  const handleRun = async (spec: PlanCandidate['spec']) => {
    if (activeRunId) return;
    setBusy(true);
    try {
      // POST to validate/create
      const res = await fetch(`${API}/experiments`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(spec),
      });
      const data = await res.json();
      if (!res.ok || data.status === 'rejected') {
        showToast(`Safety check failed: ${(data.reasons ?? []).join(', ')}`, 'error');
        return;
      }
      // Start the run
      const runRes = await fetch(`${API}/experiments/${data.experiment_id}/run`, { method: 'POST' });
      const runData = await runRes.json();
      setActiveRunId(runData.experiment_id ?? data.experiment_id);
    } catch (e: any) {
      showToast(e.message ?? 'Failed to start experiment', 'error');
    } finally {
      setBusy(false);
    }
  };

  const handleKill = async () => {
    try {
      await fetch(`${API}/kill`, { method: 'POST' });
      showToast('Kill switch activated — all chaos CRDs removed', 'info');
      setActiveRunId(null);
    } catch {
      showToast('Kill switch failed', 'error');
    }
  };

  const handleRunComplete = useCallback(async (runId: string) => {
    setActiveRunId(null);
    const data = await apiFetch<any>(`/experiments/${runId}`);
    if (data?.report) {
      setReports(prev => [data.report as Report, ...prev].slice(0, 5));
    }
    loadAll();
  }, [loadAll]);

  const srOk = health ? health.success_rate >= 0.995 : undefined;

  return (
    <div className="min-h-screen bg-[#0a0f1a] text-gray-100 font-sans">
      {/* ── Toast ─────────────────────────────────────────────────────────── */}
      {toast && (
        <div className={`fixed top-4 right-4 z-50 px-4 py-3 rounded-lg shadow-xl text-sm font-medium border
          ${toast.type === 'error' ? 'bg-red-950 border-red-700 text-red-300' : 'bg-gray-800 border-gray-600 text-gray-200'}`}>
          {toast.msg}
        </div>
      )}

      {/* ── Header ──────────────────────────────────────────────────────────── */}
      <header className="border-b border-gray-800 bg-gray-950/80 backdrop-blur sticky top-0 z-40 px-6 py-3">
        <div className="max-w-7xl mx-auto flex items-center justify-between">
          <div className="flex items-center gap-3">
            <div className="w-8 h-8 rounded-lg bg-gradient-to-br from-red-500 to-orange-600 flex items-center justify-center text-lg">💀</div>
            <div>
              <h1 className="text-lg font-bold tracking-tight bg-gradient-to-r from-red-400 to-orange-400 bg-clip-text text-transparent">
                Chaos Platform
              </h1>
              <p className="text-[10px] text-gray-500 -mt-0.5">AI Chaos Engineering · Kind/chaos-lab</p>
            </div>
          </div>

          <div className="flex items-center gap-6">
            {health ? (
              <>
                <MetricPill label="Success Rate" value={`${(health.success_rate * 100).toFixed(2)}%`} ok={srOk} />
                <MetricPill label="p50" value={`${health.p50_latency_ms.toFixed(0)}ms`} />
                <MetricPill label="p95" value={`${health.p95_latency_ms.toFixed(0)}ms`} />
                {health.pods && (
                  <MetricPill
                    label="Pods"
                    value={`${health.pods.ready_pods}/${health.pods.total_pods}`}
                    ok={health.pods.ready_pods === health.pods.total_pods}
                  />
                )}
              </>
            ) : (
              <span className="text-gray-500 text-sm animate-pulse">Connecting to API…</span>
            )}

            <button
              onClick={handleKill}
              className="flex items-center gap-2 bg-red-600 hover:bg-red-500 active:scale-95 text-white px-4 py-2 rounded-lg font-bold text-sm shadow-lg transition-all border border-red-400/20"
            >
              🛑 KILL
            </button>
          </div>
        </div>
      </header>

      {/* ── Body ────────────────────────────────────────────────────────────── */}
      <main className="max-w-7xl mx-auto px-6 py-6 grid grid-cols-1 lg:grid-cols-3 gap-6">

        {/* Left: Topology + LiveRun + Reports */}
        <div className="lg:col-span-2 space-y-6">

          {/* Topology */}
          <section className="bg-gray-900 rounded-xl border border-gray-800 p-4">
            <div className="flex items-center justify-between mb-3">
              <h2 className="text-sm font-semibold text-gray-300 flex items-center gap-2">
                <span className="text-blue-400">⬡</span> Service Topology
                {graph && <span className="text-xs text-gray-500 font-normal ml-1">({graph.nodes?.length ?? 0} services, {graph.edges?.length ?? 0} edges)</span>}
              </h2>
              <button onClick={loadAll} className="text-gray-600 hover:text-gray-400 transition text-xs">↻ Refresh</button>
            </div>
            <ErrorBoundary>
              <Topology graphData={graph} />
            </ErrorBoundary>
          </section>

          {/* Live Run */}
          <section>
            <ErrorBoundary>
              <LiveRun activeRunId={activeRunId} onRunComplete={handleRunComplete} />
            </ErrorBoundary>
          </section>

          {/* Reports */}
          {reports.length > 0 && (
            <section className="space-y-3">
              <h2 className="text-sm font-semibold text-gray-400 uppercase tracking-wider">Recent Reports</h2>
              {reports.map((r, i) => (
                <div key={i} className="bg-gray-900 rounded-xl border border-gray-800 p-4">
                  <div className="flex items-center justify-between mb-2">
                    <VerdictBadge verdict={r.verdict} />
                    <span className="font-mono text-xs text-gray-500">{r.experiment_id}</span>
                  </div>
                  <p className="text-sm text-gray-300 mb-3">{r.interpretation}</p>
                  {r.weakness && (
                    <div className="mb-2">
                      <span className="text-xs text-orange-500 font-semibold uppercase tracking-wider">Weakness · </span>
                      <span className="text-xs text-orange-300 font-mono">{r.weakness}</span>
                    </div>
                  )}
                  {r.recommendations?.length ? (
                    <ul className="space-y-1">
                      {r.recommendations.map((rec, j) => (
                        <li key={j} className="text-xs text-blue-300 flex gap-1.5">
                          <span className="text-blue-500 mt-0.5">›</span>{rec}
                        </li>
                      ))}
                    </ul>
                  ) : null}
                </div>
              ))}
            </section>
          )}
        </div>

        {/* Right: Planner */}
        <aside className="space-y-4">
          <div className="sticky top-20">
            <h2 className="text-sm font-semibold text-gray-400 uppercase tracking-wider mb-3 flex items-center gap-2">
              <span className="text-yellow-400">◈</span> Experiment Planner
              {activeRunId && <span className="ml-auto text-xs text-orange-400 animate-pulse">● Running</span>}
            </h2>

            {candidates.length === 0 ? (
              <div className="text-center py-12 text-gray-600 text-sm">
                <div className="text-3xl mb-2">🔍</div>
                Loading candidates…
              </div>
            ) : (
              <div className="space-y-3">
                {candidates.map((c, i) => (
                  <div key={i} className="bg-gray-900 rounded-xl border border-gray-800 p-4 hover:border-gray-700 transition-colors">
                    <div className="flex justify-between items-start mb-1">
                      <h3 className="text-sm font-semibold text-white leading-snug">{c.spec.name}</h3>
                      <span className="ml-2 shrink-0 text-xs font-mono text-yellow-400 bg-yellow-900/30 px-1.5 py-0.5 rounded">
                        {c.score.toFixed(2)}
                      </span>
                    </div>

                    <p className="text-xs text-gray-400 mb-3 leading-relaxed">{c.spec.hypothesis}</p>

                    {/* Score bars */}
                    <div className="space-y-1.5 mb-3">
                      <div className="flex justify-between text-[10px] text-gray-500">
                        <span>Impact</span><span>{c.impact.toFixed(2)}</span>
                      </div>
                      <ScoreBar value={c.impact} color="bg-emerald-500" />

                      <div className="flex justify-between text-[10px] text-gray-500 mt-1">
                        <span>Risk</span><span>{c.risk.toFixed(2)}</span>
                      </div>
                      <ScoreBar value={c.risk} color="bg-red-500" />
                    </div>

                    <p className="text-[10px] text-gray-600 italic mb-3">"{c.rationale}"</p>

                    <button
                      onClick={() => handleRun(c.spec)}
                      disabled={!!activeRunId || busy}
                      className="w-full py-1.5 rounded-lg text-sm font-semibold transition-all
                        bg-blue-600 hover:bg-blue-500 text-white
                        disabled:bg-gray-700 disabled:text-gray-500 disabled:cursor-not-allowed
                        active:scale-95"
                    >
                      {activeRunId ? '⬤ Running…' : '▶ Run'}
                    </button>
                  </div>
                ))}
              </div>
            )}
          </div>
        </aside>
      </main>
    </div>
  );
}
