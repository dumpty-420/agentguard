import { Activity, LayoutList, Server, Zap } from 'lucide-react';
import MetricsCard from '../components/MetricsCard';
import RunTable from '../components/RunTable';

export default function Overview({ runs, totalSteps, inspectRun }) {
  const avgSteps = runs.length > 0 ? (totalSteps / runs.length).toFixed(1) : '0';

  return (
    <div className="flex flex-col gap-6">
      <div>
        <h2 className="text-2xl font-bold text-white mb-2">Overview</h2>
        <p className="text-brand-muted">Monitor AgentGuard runtime metrics and recent session history.</p>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-4">
        <MetricsCard title="Total Runs" value={runs.length} icon={Activity} />
        <MetricsCard title="Total Steps" value={totalSteps} icon={LayoutList} />
        <MetricsCard title="Avg Steps / Run" value={avgSteps} icon={Zap} />
        <MetricsCard title="Providers" value="Anthropic" icon={Server} />
      </div>

      <div className="mt-4">
        <h3 className="text-lg font-semibold text-white mb-4">Recent Runs</h3>
        <RunTable runs={runs} onInspect={inspectRun} />
      </div>
    </div>
  );
}
