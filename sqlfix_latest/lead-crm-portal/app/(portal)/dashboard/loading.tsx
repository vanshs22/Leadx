export default function Loading() {
  return (
    <div className="page" aria-busy="true" aria-live="polite">
      <span className="sr-only">Loading workspace overview…</span>
      <div className="page-header" aria-hidden="true">
        <div className="min-w-0 flex-1">
          <div className="skeleton h-3 w-32" />
          <div className="skeleton mt-2.5 h-7 w-72 max-w-full" />
          <div className="skeleton mt-2.5 h-4 w-[28rem] max-w-full" />
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
            <div className="skeleton mt-3 h-3 w-24" />
          </div>
        ))}
      </div>
      <div className="grid gap-4 lg:grid-cols-3" aria-hidden="true">
        <div className="panel lg:col-span-2">
          <div className="panel-header"><div className="skeleton h-4 w-44" /></div>
          <div className="grid gap-3 p-5 sm:grid-cols-3">
            {[0, 1, 2].map((i) => <div key={i} className="skeleton h-20" />)}
          </div>
        </div>
        <div className="panel">
          <div className="panel-header"><div className="skeleton h-4 w-28" /></div>
          <div className="space-y-2 p-5">
            {[0, 1, 2].map((i) => <div key={i} className="skeleton h-12" />)}
          </div>
        </div>
      </div>
      <div className="grid gap-4 lg:grid-cols-2" aria-hidden="true">
        {[0, 1].map((p) => (
          <div key={p} className="panel">
            <div className="panel-header"><div className="skeleton h-4 w-32" /></div>
            <div className="space-y-3 p-5">
              {[0, 1, 2, 3].map((i) => <div key={i} className="skeleton h-4" style={{ width: `${90 - i * 12}%` }} />)}
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}
