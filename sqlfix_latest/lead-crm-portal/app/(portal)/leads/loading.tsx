export default function Loading() {
  return (
    <div className="page" aria-busy="true" aria-label="Loading leads">
      <span className="sr-only">Loading leads…</span>
      <div className="page-header">
        <div className="space-y-2">
          <div className="skeleton h-3 w-20" />
          <div className="skeleton h-7 w-56" />
          <div className="skeleton h-4 w-80 max-w-full" />
        </div>
        <div className="flex gap-2">
          <div className="skeleton h-9 w-28" />
          <div className="skeleton h-9 w-28" />
        </div>
      </div>
      <div className="skeleton h-14 w-full rounded-xl" />
      <div className="panel">
        <div className="h-10 border-b border-line bg-surface-3" />
        <div className="divide-y divide-line">
          {Array.from({ length: 8 }).map((_, i) => (
            <div key={i} className="flex items-center gap-4 px-4 py-3">
              <div className="skeleton h-4 w-4" />
              <div className="skeleton h-3 w-1/4" />
              <div className="skeleton h-3 w-1/6" />
              <div className="skeleton h-3 w-16" />
              <div className="ml-auto skeleton h-3 w-10" />
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
