export default function MetricsCard({ title, value, icon: Icon }) {
  return (
    <div className="bg-brand-surface border border-brand-border rounded-xl p-5 flex items-center justify-between shadow-sm">
      <div className="flex flex-col gap-1">
        <span className="text-sm text-brand-muted font-medium">{title}</span>
        <span className="text-3xl font-semibold text-brand-text">{value}</span>
      </div>
      <div className="p-3 bg-brand-bg rounded-lg">
        <Icon className="w-6 h-6 text-brand-primary" />
      </div>
    </div>
  );
}
