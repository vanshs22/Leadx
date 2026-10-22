export default function GrowthLoading() {
  return (
    <div className="page" aria-busy="true">
      <div className="skeleton h-10 w-64" />
      <div className="skeleton mt-2 h-5 w-96 max-w-full" />
      <div className="mt-6 grid gap-4 sm:grid-cols-2 xl:grid-cols-5">
        {Array.from({ length: 5 }).map((_, index) => <div key={index} className="card-pad"><div className="skeleton h-3 w-20" /><div className="skeleton mt-3 h-8 w-16" /></div>)}
      </div>
      <div className="mt-6 grid gap-5 lg:grid-cols-2"><div className="card-pad min-h-72"><div className="skeleton h-5 w-48" /></div><div className="card-pad min-h-72"><div className="skeleton h-5 w-40" /></div></div>
    </div>
  );
}
