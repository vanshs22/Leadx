export default function Loading() {
  return (
    <div className="page" aria-busy="true" aria-live="polite">
      <span className="sr-only">Loading…</span>
      <div className="border-b border-line pb-5">
        <div className="skeleton h-3 w-24 rounded" />
        <div className="skeleton mt-3 h-7 w-56 rounded-md" />
        <div className="skeleton mt-2.5 h-4 w-80 max-w-full rounded" />
      </div>
      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        {[0, 1, 2, 3].map((i) => (
          <div key={i} className="card-pad">
            <div className="skeleton h-3 w-20 rounded" />
            <div className="skeleton mt-3 h-6 w-16 rounded" />
          </div>
        ))}
      </div>
      <div className="grid gap-4 lg:grid-cols-3">
        <div className="panel lg:col-span-2">
          <div className="panel-header"><div className="skeleton h-4 w-40 rounded" /></div>
          <div className="divide-y divide-line">
            {[0, 1, 2, 3, 4].map((i) => <div key={i} className="flex gap-4 px-5 py-3.5"><div className="skeleton h-3 w-1/4" /><div className="skeleton h-3 w-1/6" /><div className="skeleton h-3 w-1/5" /></div>)}
          </div>
        </div>
        <div className="panel">
          <div className="panel-header"><div className="skeleton h-4 w-28 rounded" /></div>
          <div className="space-y-3 p-5">{[0, 1, 2].map((i) => <div key={i} className="skeleton h-10 rounded-lg" />)}</div>
        </div>
      </div>
    </div>
  );
}
