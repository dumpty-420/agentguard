# AgentGuard Frontend

This is the production-grade React + Vite frontend for the AgentGuard control plane. It provides a developer-focused dashboard to monitor agent runs, trigger new workflows, resume from checkpoints, and inspect pipeline states.

## Prerequisites
- Node.js (v18+)

## Setup

1. **Install dependencies:**
   ```bash
   cd frontend
   npm install
   ```

2. **Run the development server:**
   ```bash
   npm run dev
   ```

   The app will start at `http://localhost:5173`. 
   *Note: In development, API calls to `/api` are automatically proxied to `https://agentguard-dl7kvw447a-el.a.run.app` via `vite.config.js` to avoid CORS issues.*

## Configuration

By default, the application connects to the backend through the Vite proxy (to the Cloud Run deployment). If you need to point the frontend to a different backend URL, set the `VITE_API_URL` environment variable:

```bash
VITE_API_URL=https://your-production-api.com npm run build
```

## Stack
- **React 19**
- **Vite**
- **Tailwind CSS v4** (Dark theme custom design system)
- **Lucide React** (Icons)
