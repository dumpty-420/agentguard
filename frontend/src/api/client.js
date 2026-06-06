const API_BASE = import.meta.env.VITE_API_URL || '/api';

export async function checkHealth() {
  try {
    const res = await fetch(`${API_BASE}/health`);
    if (!res.ok) throw new Error(`HTTP error! status: ${res.status}`);
    return await res.json();
  } catch (error) {
    throw new Error('Failed to connect to AgentGuard backend');
  }
}

export async function startRun(topic, user_id) {
  const res = await fetch(`${API_BASE}/run`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ topic, user_id }),
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new Error(err.detail || `HTTP error! status: ${res.status}`);
  }
  return await res.json();
}

export async function resumeRun(run_id, user_id) {
  const res = await fetch(`${API_BASE}/resume`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ run_id, user_id }),
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new Error(err.detail || `HTTP error! status: ${res.status}`);
  }
  return await res.json();
}

export async function inspectRun(run_id) {
  const res = await fetch(`${API_BASE}/run/${run_id}`);
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new Error(err.detail || `HTTP error! status: ${res.status}`);
  }
  return await res.json();
}
