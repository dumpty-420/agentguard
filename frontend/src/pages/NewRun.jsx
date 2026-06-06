import { useState } from 'react';
import { Copy, Loader2, Play } from 'lucide-react';
import { startRun } from '../api/client';
import RunPipeline from '../components/RunPipeline';

export default function NewRun({ onRunComplete }) {
  const [topic, setTopic] = useState('');
  const [userId, setUserId] = useState('user-123');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);
  const [result, setResult] = useState(null);

  const handleSubmit = async (e) => {
    e.preventDefault();
    setLoading(true);
    setError(null);
    setResult(null);

    try {
      const data = await startRun(topic, userId);
      setResult(data);
      onRunComplete(data);
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  };

  const copyRunId = () => {
    if (result) navigator.clipboard.writeText(result.run_id);
  };

  return (
    <div className="flex flex-col gap-6 max-w-4xl">
      <div>
        <h2 className="text-2xl font-bold text-white mb-2">New Run</h2>
        <p className="text-brand-muted">Initiate a new agent workflow through the runtime control plane.</p>
      </div>

      <div className="bg-brand-surface border border-brand-border rounded-xl p-6">
        <form onSubmit={handleSubmit} className="flex flex-col gap-4">
          <div className="flex flex-col gap-2">
            <label className="text-sm font-medium text-brand-text">Research Topic</label>
            <textarea
              required
              value={topic}
              onChange={(e) => setTopic(e.target.value)}
              placeholder="Enter a research topic (e.g. quantum computing advancements in 2024)"
              className="w-full bg-brand-bg border border-brand-border rounded-lg px-4 py-3 text-sm text-brand-text placeholder-brand-muted focus:outline-none focus:border-brand-primary focus:ring-1 focus:ring-brand-primary h-24 resize-none transition-colors"
            />
          </div>
          
          <div className="flex flex-col gap-2">
            <label className="text-sm font-medium text-brand-text">User ID</label>
            <input
              required
              type="text"
              value={userId}
              onChange={(e) => setUserId(e.target.value)}
              className="w-full max-w-xs bg-brand-bg border border-brand-border rounded-lg px-4 py-2.5 text-sm text-brand-text focus:outline-none focus:border-brand-primary focus:ring-1 focus:ring-brand-primary transition-colors"
            />
          </div>

          {error && (
            <div className="bg-brand-danger/10 border border-brand-danger/20 text-brand-danger text-sm px-4 py-3 rounded-lg">
              {error}
            </div>
          )}

          <div className="pt-2">
            <button
              type="submit"
              disabled={loading || !topic}
              className="flex items-center gap-2 bg-brand-primary hover:bg-brand-primary-hover text-white px-5 py-2.5 rounded-lg text-sm font-medium transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
            >
              {loading ? <Loader2 className="w-4 h-4 animate-spin" /> : <Play className="w-4 h-4" />}
              Start Agent Run
            </button>
          </div>
        </form>
      </div>

      {result && (
        <div className="flex flex-col gap-4 animate-in fade-in slide-in-from-bottom-4 duration-500">
          <div className="flex items-center justify-between">
            <h3 className="text-lg font-semibold text-white">Execution Result</h3>
            <div className="flex items-center gap-2 bg-brand-surface border border-brand-border px-3 py-1.5 rounded-lg text-sm font-mono text-brand-muted">
              <span>Run ID:</span>
              <span className="text-brand-text">{result.run_id}</span>
              <button onClick={copyRunId} className="hover:text-white transition-colors ml-2" title="Copy Run ID">
                <Copy className="w-4 h-4" />
              </button>
            </div>
          </div>
          
          <div className="bg-brand-surface border border-brand-border rounded-xl p-6">
            <h4 className="text-sm font-medium text-brand-muted mb-4 uppercase tracking-wider">Pipeline Steps</h4>
            <RunPipeline completedSteps={result.completed_steps} />
          </div>

          <div className="bg-brand-surface border border-brand-border rounded-xl overflow-hidden flex flex-col">
            <div className="bg-brand-bg/50 px-4 py-3 border-b border-brand-border">
              <h4 className="text-sm font-medium text-brand-text">Output Log</h4>
            </div>
            <div className="p-4 max-h-96 overflow-y-auto">
              <pre className="text-xs font-mono text-brand-muted whitespace-pre-wrap">
                {result.messages.map((msg, i) => (
                  <div key={i} className="mb-2 last:mb-0 pb-2 border-b border-brand-border/30 last:border-0">
                    <span className="text-brand-primary mr-2">[{new Date().toISOString()}]</span>
                    {msg}
                  </div>
                ))}
              </pre>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
