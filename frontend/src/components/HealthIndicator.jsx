import { useState, useEffect } from 'react';
import { checkHealth } from '../api/client';
import { Activity } from 'lucide-react';

export default function HealthIndicator() {
  const [status, setStatus] = useState('loading');
  const [version, setVersion] = useState('');

  useEffect(() => {
    let mounted = true;
    
    const pollHealth = async () => {
      try {
        const data = await checkHealth();
        if (mounted) {
          setStatus('online');
          setVersion(data.version);
        }
      } catch (err) {
        if (mounted) setStatus('offline');
      }
    };

    pollHealth();
    const interval = setInterval(pollHealth, 30000);
    return () => {
      mounted = false;
      clearInterval(interval);
    };
  }, []);

  const getStatusColor = () => {
    if (status === 'online') return 'bg-brand-success';
    if (status === 'offline') return 'bg-brand-danger';
    return 'bg-brand-muted';
  };

  return (
    <div className="flex items-center gap-2 px-3 py-1.5 rounded-full bg-brand-surface border border-brand-border text-xs font-medium text-brand-text w-max">
      <div className={`w-2 h-2 rounded-full ${getStatusColor()}`} />
      <span>AgentGuard {status === 'online' ? 'Online' : 'Offline'} {version && `(v${version})`}</span>
      {status === 'loading' && <Activity className="w-3 h-3 animate-spin text-brand-muted" />}
    </div>
  );
}
