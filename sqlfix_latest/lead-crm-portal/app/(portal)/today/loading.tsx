export default function Loading() {
  return (
    <div className="page" aria-busy="true" aria-live="polite">
      <span className="sr-only">Loading today&apos;s action queue…</span>
      <div className="page-header" aria-hidden="true">
        <div className="min-w-0 flex-1">
          <div className="skeleton h-3 w-36" />
          <div className="skeleton mt-2.5 h-7 w-40" />
          <div className="skeleton mt-2.5 h-4 w-96 max-w-full" />
        </div>
        <div className="skeleton h-9 w-24" />
      </div>
      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4" aria-hidden="true">
        {[0, 1, 2, 3].map((i) => (
          <div key={i} className="kpi">
            <div className="skeleton h-3 w-20" />
            <div className="skeleton mt-3 h-7 w-14" />
          </div>
        ))}
      </div>
      <div className="panel" aria-hidden="true">
        <div className="panel-header"><div><div className="skeleton h-4 w-32" /><div className="skeleton mt-2 h-3 w-72 max-w-full" /></div></div>
        <div className="divide-y divide-line">
          {[0, 1, 2, 3, 4].map((i) => (
            <div key={i} className="flex flex-wrap items-start justify-between gap-4 px-5 py-4">
              <div className="min-w-0 flex-1 space-y-2">
                <div className="skeleton h-4 w-48" />
                <div className="skeleton h-3 w-32" />
                <div className="skeleton h-7 w-64 max-w-full" />
              </div>
              <div className="flex gap-2">
                {[0, 1, 2].map((b) => <div key={b} className="skeleton h-8 w-20" />)}
              </div>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
