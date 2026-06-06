import { CheckCircle2, Clock, PlayCircle } from 'lucide-react';

export default function RunTable({ runs, onInspect }) {
  if (runs.length === 0) {
    return (
      <div className="flex flex-col items-center justify-center p-12 bg-brand-surface border border-brand-border rounded-xl">
        <Clock className="w-10 h-10 text-brand-muted mb-4" />
        <p className="text-brand-text font-medium">No runs yet</p>
        <p className="text-sm text-brand-muted mt-1">Start a new run to see history.</p>
      </div>
    );
  }

  return (
    <div className="overflow-hidden bg-brand-surface border border-brand-border rounded-xl">
      <table className="w-full text-left border-collapse">
        <thead>
          <tr className="bg-brand-bg/50 border-b border-brand-border text-brand-muted text-xs uppercase tracking-wider">
            <th className="px-6 py-4 font-medium">Run ID</th>
            <th className="px-6 py-4 font-medium">Steps Completed</th>
            <th className="px-6 py-4 font-medium">Timestamp</th>
            <th className="px-6 py-4 font-medium">Status</th>
            <th className="px-6 py-4 font-medium text-right">Action</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-brand-border text-sm">
          {runs.map((run) => (
            <tr key={run.run_id} className="hover:bg-brand-bg/50 transition-colors">
              <td className="px-6 py-4 font-mono text-brand-text">{run.run_id}</td>
              <td className="px-6 py-4 text-brand-muted">
                {run.completed_steps.length} / 3
              </td>
              <td className="px-6 py-4 text-brand-muted">
                {run.timestamp.toLocaleTimeString()}
              </td>
              <td className="px-6 py-4">
                <div className="flex items-center gap-2">
                  <CheckCircle2 className="w-4 h-4 text-brand-success" />
                  <span className="text-brand-text">Success</span>
                </div>
              </td>
              <td className="px-6 py-4 text-right">
                <button
                  onClick={() => onInspect(run.run_id)}
                  className="flex items-center justify-end gap-1.5 text-brand-primary hover:text-brand-primary-hover font-medium ml-auto"
                >
                  Inspect <PlayCircle className="w-4 h-4" />
                </button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
