import { useState, useEffect } from 'react';
import { Loader2, Search, CheckCircle2, Circle, AlertCircle } from 'lucide-react';
import { inspectRun } from '../api/client';
import RunPipeline from '../components/RunPipeline';

export default function InspectRun({ initialRunId = '' }) {
  const [runId, setRunId] = useState(initialRunId);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);
  const [result, setResult] = useState(null);

  // Auto-fetch if initialRunId is provided
  useEffect(() => {
    if (initialRunId) {
      handleInspect(initialRunId);
    }
  }, [initialRunId]);

  const handleSubmit = (e) => {
    e.preventDefault();
    handleInspect(runId);
  };

  const handleInspect = async (idToInspect) => {
    if (!idToInspect) return;
    
    setLoading(true);
    setError(null);
    setResult(null);

    try {
      const data = await inspectRun(idToInspect);
      setResult(data);
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="flex flex-col gap-6 max-w-4xl">
      <div>
        <h2 className="text-2xl font-bold text-white mb-2">Inspect Run</h2>
        <p className="text-brand-muted">Query the Postgres checkpoint store to view the current state of a run.</p>
      </div>

      <div className="bg-brand-surface border border-brand-border rounded-xl p-6">
        <form onSubmit={handleSubmit} className="flex gap-4 items-end">
          <div className="flex flex-col gap-2 flex-1 max-w-md">
            <label className="text-sm font-medium text-brand-text">Run ID</label>
            <input
              required
              type="text"
              value={runId}
              onChange={(e) => setRunId(e.target.value)}
              placeholder="e.g. run-1a2b3c4d5e"
              className="w-full bg-brand-bg border border-brand-border rounded-lg px-4 py-2.5 text-sm font-mono text-brand-text focus:outline-none focus:border-brand-primary focus:ring-1 focus:ring-brand-primary transition-colors"
            />
          </div>
          
          <button
            type="submit"
            disabled={loading || !runId}
            className="flex items-center gap-2 bg-brand-primary hover:bg-brand-primary-hover text-white px-5 py-2.5 rounded-lg text-sm font-medium transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
          >
            {loading ? <Loader2 className="w-4 h-4 animate-spin" /> : <Search className="w-4 h-4" />}
            Inspect
          </button>
        </form>

        {error && (
          <div className="mt-4 bg-brand-danger/10 border border-brand-danger/20 text-brand-danger text-sm px-4 py-3 rounded-lg">
            {error}
          </div>
        )}
      </div>

      {result && (
        <div className="flex flex-col gap-4 animate-in fade-in slide-in-from-bottom-4 duration-500">
          {!result.has_checkpoint ? (
            <div className="flex flex-col items-center justify-center p-12 bg-brand-surface border border-brand-border rounded-xl">
              <AlertCircle className="w-10 h-10 text-brand-muted mb-4" />
              <p className="text-brand-text font-medium text-lg">No checkpoint found</p>
              <p className="text-sm text-brand-muted mt-1">There is no checkpoint data in Postgres for <span className="font-mono text-brand-text">{result.run_id}</span>.</p>
            </div>
          ) : (
            <>
              <div className="flex items-center justify-between">
                <div className="flex items-center gap-3">
                  <h3 className="text-lg font-semibold text-white">Checkpoint State</h3>
                  <span className="px-2.5 py-1 rounded-md bg-brand-success/20 text-brand-success text-xs font-medium border border-brand-success/30 flex items-center gap-1.5">
                    <CheckCircle2 className="w-3 h-3" /> Checkpoint exists
                  </span>
                </div>
              </div>
              
              <div className="bg-brand-surface border border-brand-border rounded-xl p-6">
                <h4 className="text-sm font-medium text-brand-muted mb-4 uppercase tracking-wider">Current Pipeline State</h4>
                <RunPipeline completedSteps={result.completed_steps} />
              </div>

              <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                <div className="bg-brand-surface border border-brand-border rounded-xl p-5">
                  <h4 className="text-sm font-medium text-brand-muted mb-3 uppercase tracking-wider">Completed Steps</h4>
                  {result.completed_steps.length > 0 ? (
                    <ul className="flex flex-col gap-2">
                      {result.completed_steps.map(step => (
                        <li key={step} className="flex items-center gap-2 text-sm text-brand-text font-mono">
                          <CheckCircle2 className="w-4 h-4 text-brand-success" /> {step}
                        </li>
                      ))}
                    </ul>
                  ) : (
                    <span className="text-sm text-brand-muted italic">None</span>
                  )}
                </div>
                
                <div className="bg-brand-surface border border-brand-border rounded-xl p-5">
                  <h4 className="text-sm font-medium text-brand-muted mb-3 uppercase tracking-wider">Next Pending Steps</h4>
                  {result.next_steps.length > 0 ? (
                    <ul className="flex flex-col gap-2">
                      {result.next_steps.map(step => (
                        <li key={step} className="flex items-center gap-2 text-sm text-brand-text font-mono">
                          <Circle className="w-4 h-4 text-brand-muted" /> {step}
                        </li>
                      ))}
                    </ul>
                  ) : (
                    <span className="text-sm text-brand-muted italic">Run is fully completed</span>
                  )}
                </div>
              </div>
            </>
          )}
        </div>
      )}
    </div>
  );
}
