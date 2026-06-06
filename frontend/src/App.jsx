import { useState } from 'react';
import Sidebar from './components/Sidebar';
import Overview from './pages/Overview';
import NewRun from './pages/NewRun';
import ResumeRun from './pages/ResumeRun';
import InspectRun from './pages/InspectRun';

function App() {
  const [currentView, setView] = useState('overview');
  const [runs, setRuns] = useState([]);
  const [totalSteps, setTotalSteps] = useState(0);
  const [inspectRunId, setInspectRunId] = useState('');

  const handleRunComplete = (runData) => {
    setRuns(prev => {
      // Check if this run_id is already in the list (e.g. from resume)
      const existing = prev.find(r => r.run_id === runData.run_id);
      if (existing) {
        // Replace existing
        return prev.map(r => r.run_id === runData.run_id ? { ...runData, timestamp: new Date() } : r);
      }
      return [{ ...runData, timestamp: new Date() }, ...prev];
    });
    setTotalSteps(prev => prev + runData.completed_steps.length);
  };

  const handleInspectRequest = (runId) => {
    setInspectRunId(runId);
    setView('inspect_run');
  };

  return (
    <div className="flex min-h-screen bg-brand-bg text-brand-text">
      <Sidebar currentView={currentView} setView={setView} />
      
      <main className="flex-1 ml-64 p-8 overflow-y-auto h-screen">
        <div className="max-w-6xl mx-auto">
          {currentView === 'overview' && (
            <Overview runs={runs} totalSteps={totalSteps} inspectRun={handleInspectRequest} />
          )}
          {currentView === 'new_run' && (
            <NewRun onRunComplete={handleRunComplete} />
          )}
          {currentView === 'resume_run' && (
            <ResumeRun onRunComplete={handleRunComplete} />
          )}
          {currentView === 'inspect_run' && (
            <InspectRun initialRunId={inspectRunId} />
          )}
        </div>
      </main>
    </div>
  );
}

export default App;
