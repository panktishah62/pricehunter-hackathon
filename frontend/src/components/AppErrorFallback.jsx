export default function AppErrorFallback() {
  return (
    <main className="flex min-h-screen items-center justify-center bg-slate-950 px-6 text-white">
      <section className="max-w-md rounded-lg border border-white/10 bg-white/10 p-6 shadow-2xl">
        <p className="text-sm uppercase tracking-[0.24em] text-slate-300">Something broke</p>
        <h1 className="mt-3 text-2xl font-semibold">Please refresh and try again.</h1>
      </section>
    </main>
  )
}
