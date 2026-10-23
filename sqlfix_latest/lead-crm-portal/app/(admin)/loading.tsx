export default function Loading() {
  return (
    <div className="page" aria-busy="true" aria-live="polite">
      <div className="skeleton h-8 w-56 rounded-lg" />
      <div className="skeleton mt-2 h-4 w-80 max-w-full rounded-md" />
      <div className="mt-6 grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        {[0, 1, 2, 3].map((i) => <div key={i} className="skeleton h-24 rounded-2xl" />)}
      </div>
      <div className="mt-6 grid gap-4 lg:grid-cols-3">
        <div className="skeleton h-64 rounded-2xl lg:col-span-2" />
        <div className="skeleton h-64 rounded-2xl" />
      </div>
    </div>
  );
}
