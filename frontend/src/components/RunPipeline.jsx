import { CheckCircle2, Circle } from 'lucide-react';

const PIPELINE_STEPS = ['researcher', 'analyst', 'synthesizer'];

export default function RunPipeline({ completedSteps }) {
  // Extract just the step names if they come in a specific format, or assume they match the PIPELINE_STEPS
  const isCompleted = (step) => completedSteps.includes(step);

  return (
    <div className="flex items-center gap-4 py-4 w-full overflow-x-auto">
      {PIPELINE_STEPS.map((step, idx) => {
        const done = isCompleted(step);
        return (
          <div key={step} className="flex items-center gap-4">
            <div className={`flex items-center gap-2 px-4 py-2 rounded-lg border ${
              done ? 'bg-brand-primary/10 border-brand-primary text-brand-primary' : 'bg-brand-bg border-brand-border text-brand-muted'
            }`}>
              {done ? <CheckCircle2 className="w-5 h-5" /> : <Circle className="w-5 h-5" />}
              <span className="font-mono text-sm uppercase tracking-wider">{step}</span>
            </div>
            {idx < PIPELINE_STEPS.length - 1 && (
              <div className={`h-[2px] w-8 ${done ? 'bg-brand-primary' : 'bg-brand-border'}`} />
            )}
          </div>
        );
      })}
    </div>
  );
}
