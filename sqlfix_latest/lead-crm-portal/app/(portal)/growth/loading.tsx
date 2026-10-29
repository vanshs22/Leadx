export default function GrowthLoading() {
  return (
    <div className="page" aria-busy="true" aria-live="polite">
      <span className="sr-only">Loading growth workspace…</span>
      <div className="page-header" aria-hidden="true">
        <div className="min-w-0 flex-1">
          <div className="skeleton h-3 w-28" />
          <div className="skeleton mt-2.5 h-7 w-72 max-w-full" />
          <div className="skeleton mt-2.5 h-4 w-[30rem] max-w-full" />
        </div>
        <div className="skeleton h-9 w-24" />
      </div>
      <div className="skeleton h-36 rounded-xl" aria-hidden="true" />
      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-5" aria-hidden="true">
        {Array.from({ length: 5 }).map((_, index) => (
          <div key={index} className="kpi">
            <div className="skeleton h-3 w-20" />
            <div className="skeleton mt-3 h-7 w-16" />
            <div className="skeleton mt-3 h-3 w-24" />
          </div>
        ))}
      </div>
      <div className="grid gap-4 lg:grid-cols-3" aria-hidden="true">
        <div className="panel lg:col-span-2">
          <div className="panel-header"><div className="skeleton h-4 w-40" /></div>
          <div className="grid gap-3 p-5 sm:grid-cols-2">
            {Array.from({ length: 6 }).map((_, index) => <div key={index}><div className="skeleton h-3 w-20" /><div className="skeleton mt-2 h-9" /></div>)}
          </div>
        </div>
        <div className="panel">
          <div className="panel-header"><div className="skeleton h-4 w-36" /></div>
          <div className="grid grid-cols-2 gap-3 p-5">
            {Array.from({ length: 4 }).map((_, index) => <div key={index} className="skeleton h-16" />)}
          </div>
        </div>
      </div>
    </div>
  );
}
