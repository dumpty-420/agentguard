import { LayoutDashboard, PlaySquare, RotateCcw, Search } from 'lucide-react';
import HealthIndicator from './HealthIndicator';

export default function Sidebar({ currentView, setView }) {
  const navItems = [
    { id: 'overview', label: 'Overview', icon: LayoutDashboard },
    { id: 'new_run', label: 'New Run', icon: PlaySquare },
    { id: 'resume_run', label: 'Resume Run', icon: RotateCcw },
    { id: 'inspect_run', label: 'Inspect Run', icon: Search },
  ];

  return (
    <aside className="w-64 border-r border-brand-border bg-brand-surface flex flex-col h-screen fixed top-0 left-0">
      <div className="p-6 border-b border-brand-border">
        <h1 className="text-xl font-bold tracking-tight text-white flex items-center gap-2">
          <div className="w-6 h-6 rounded bg-brand-primary flex items-center justify-center">
            <span className="text-white text-xs font-bold font-mono">AG</span>
          </div>
          AgentGuard
        </h1>
      </div>
      
      <nav className="p-4 flex-1 flex flex-col gap-2">
        <div className="text-xs font-semibold text-brand-muted uppercase tracking-wider mb-2 px-3">
          Dashboard
        </div>
        {navItems.map((item) => (
          <button
            key={item.id}
            onClick={() => setView(item.id)}
            className={`w-full flex items-center gap-3 px-3 py-2.5 rounded-lg text-sm font-medium transition-colors ${
              currentView === item.id 
                ? 'bg-brand-primary/10 text-brand-primary' 
                : 'text-brand-text hover:bg-brand-bg hover:text-white'
            }`}
          >
            <item.icon className={`w-5 h-5 ${currentView === item.id ? 'text-brand-primary' : 'text-brand-muted'}`} />
            {item.label}
          </button>
        ))}
      </nav>

      <div className="p-4 border-t border-brand-border">
        <HealthIndicator />
      </div>
    </aside>
  );
}
