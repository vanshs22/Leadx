export default function Loading() {
  return (
    <div className="page" aria-busy="true" aria-live="polite">
      <span className="sr-only">Loading workspace…</span>
      <div className="page-header" aria-hidden="true">
        <div className="min-w-0 flex-1">
          <div className="skeleton h-3 w-24" />
          <div className="skeleton mt-2.5 h-7 w-64 max-w-full" />
          <div className="skeleton mt-2.5 h-4 w-96 max-w-full" />
        </div>
        <div className="flex gap-2">
          <div className="skeleton h-9 w-24" />
          <div className="skeleton h-9 w-28" />
        </div>
      </div>
      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4" aria-hidden="true">
        {[0, 1, 2, 3].map((i) => (
          <div key={i} className="kpi">
            <div className="skeleton h-3 w-20" />
            <div className="skeleton mt-3 h-7 w-16" />
            <div className="skeleton mt-3 h-3 w-28" />
          </div>
        ))}
      </div>
      <div className="grid gap-4 lg:grid-cols-3" aria-hidden="true">
        <div className="panel lg:col-span-2">
          <div className="panel-header"><div className="skeleton h-4 w-40" /></div>
          <div className="space-y-3 p-5">
            {[0, 1, 2, 3, 4].map((i) => <div key={i} className="skeleton h-4" style={{ width: `${92 - i * 9}%` }} />)}
          </div>
        </div>
        <div className="panel">
          <div className="panel-header"><div className="skeleton h-4 w-28" /></div>
          <div className="space-y-3 p-5">
            {[0, 1, 2].map((i) => <div key={i} className="skeleton h-10" />)}
          </div>
        </div>
      </div>
    </div>
  );
}
