import { useEffect, useMemo, useState } from 'react'

const apiBaseUrl = import.meta.env.VITE_API_URL || 'http://localhost:8000'

function formatDate(value) {
  if (!value) return '—'
  try {
    return new Intl.DateTimeFormat('en-IN', { dateStyle: 'medium' }).format(new Date(value))
  } catch {
    return '—'
  }
}

function statusBadge(status) {
  const normalized = status || 'Pending'
  if (normalized === 'Active') return 'bg-emerald-50 text-emerald-700 border-emerald-200'
  if (normalized === 'Converted') return 'bg-blue-50 text-blue-700 border-blue-200'
  if (normalized === 'Paused') return 'bg-amber-50 text-amber-700 border-amber-200'
  return 'bg-slate-50 text-slate-600 border-slate-200'
}

function copyText(value) {
  if (!value) return
  navigator.clipboard?.writeText(value).catch(() => {})
}

function SupplierCrmDashboard() {
  const [filters, setFilters] = useState({ q: '', category: '', city: '', pilotStatus: '' })
  const [page, setPage] = useState(1)
  const [pageSize, setPageSize] = useState(50)
  const [data, setData] = useState({ suppliers: [], total: 0 })
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [generatedLinks, setGeneratedLinks] = useState({})
  const [busySupplier, setBusySupplier] = useState('')

  const queryString = useMemo(() => {
    const params = new URLSearchParams({
      limit: String(pageSize),
      offset: String((page - 1) * pageSize),
    })
    Object.entries(filters).forEach(([key, value]) => {
      if (value) params.set(key, value)
    })
    return params.toString()
  }, [filters, page, pageSize])

  const totalSuppliers = data.total || 0
  const totalPages = Math.max(1, Math.ceil(totalSuppliers / pageSize))
  const currentOffset = data.offset ?? (page - 1) * pageSize
  const startRow = totalSuppliers && data.suppliers.length ? currentOffset + 1 : 0
  const endRow = totalSuppliers && data.suppliers.length ? currentOffset + data.suppliers.length : 0

  function updateFilter(key, value) {
    setPage(1)
    setFilters((current) => ({ ...current, [key]: value }))
  }

  function goToPage(nextPage) {
    setPage(Math.min(Math.max(nextPage, 1), totalPages))
  }

  useEffect(() => {
    let cancelled = false
    async function loadSuppliers() {
      setLoading(true)
      setError('')
      try {
        const response = await fetch(`${apiBaseUrl}/api/suppliers/crm?${queryString}`)
        if (!response.ok) throw new Error('Could not load suppliers.')
        const payload = await response.json()
        if (!cancelled) setData(payload)
      } catch (err) {
        if (!cancelled) setError(err.message || 'Could not load suppliers.')
      } finally {
        if (!cancelled) setLoading(false)
      }
    }
    loadSuppliers()
    return () => {
      cancelled = true
    }
  }, [queryString])

  useEffect(() => {
    if (page > totalPages) setPage(totalPages)
  }, [page, totalPages])

  async function generatePilotLink(supplierId) {
    setBusySupplier(supplierId)
    setError('')
    try {
      const response = await fetch(`${apiBaseUrl}/api/suppliers/crm/${encodeURIComponent(supplierId)}/pilot-link`, {
        method: 'POST',
      })
      if (!response.ok) throw new Error('Could not generate pilot link.')
      const payload = await response.json()
      setGeneratedLinks((current) => ({ ...current, [supplierId]: payload }))
      copyText(payload.pilot_url)
    } catch (err) {
      setError(err.message || 'Could not generate pilot link.')
    } finally {
      setBusySupplier('')
    }
  }

  return (
    <div className="min-h-screen bg-[#f7f3ea] text-slate-950">
      <header className="border-b border-slate-200 bg-white/85 backdrop-blur">
        <div className="mx-auto flex max-w-7xl flex-col gap-4 px-4 py-5 sm:px-6 lg:flex-row lg:items-center lg:justify-between">
          <div>
            <p className="text-xs font-semibold uppercase tracking-[0.24em] text-brand">ZWIG Internal</p>
            <h1 className="mt-2 text-3xl font-semibold tracking-[-0.04em] sm:text-4xl">Supplier CRM</h1>
            <p className="mt-2 max-w-2xl text-sm leading-6 text-slate-600">
              View supplier records, generate pilot links, track onboarding, and open catalog/profile pages.
            </p>
          </div>
          <a
            className="rounded-full bg-slate-950 px-5 py-3 text-sm font-semibold text-white transition hover:bg-slate-800"
            href="/supplier-onboarding"
          >
            Supplier landing
          </a>
        </div>
      </header>

      <main className="mx-auto max-w-7xl px-4 py-6 sm:px-6">
        <section className="rounded-[2rem] border border-slate-200 bg-white p-4 shadow-[0_20px_80px_rgba(15,23,42,0.05)]">
          <div className="grid gap-3 md:grid-cols-4">
            <input
              className="rounded-2xl border border-slate-200 px-4 py-3 text-sm outline-none focus:border-brand"
              placeholder="Search supplier, phone, website"
              value={filters.q}
              onChange={(event) => updateFilter('q', event.target.value)}
            />
            <input
              className="rounded-2xl border border-slate-200 px-4 py-3 text-sm outline-none focus:border-brand"
              placeholder="Category"
              value={filters.category}
              onChange={(event) => updateFilter('category', event.target.value)}
            />
            <input
              className="rounded-2xl border border-slate-200 px-4 py-3 text-sm outline-none focus:border-brand"
              placeholder="City"
              value={filters.city}
              onChange={(event) => updateFilter('city', event.target.value)}
            />
            <select
              className="rounded-2xl border border-slate-200 px-4 py-3 text-sm outline-none focus:border-brand"
              value={filters.pilotStatus}
              onChange={(event) => updateFilter('pilotStatus', event.target.value)}
            >
              <option value="">All pilot statuses</option>
              <option value="Pending">Pending</option>
              <option value="Active">Active</option>
              <option value="Paused">Paused</option>
              <option value="Converted">Converted</option>
            </select>
          </div>
        </section>

        {error ? <div className="mt-4 rounded-2xl border border-rose-200 bg-rose-50 p-4 text-sm text-rose-700">{error}</div> : null}

        <section className="mt-5 overflow-hidden rounded-[2rem] border border-slate-200 bg-white shadow-[0_20px_80px_rgba(15,23,42,0.05)]">
          <div className="flex flex-col gap-4 border-b border-slate-200 px-5 py-4 lg:flex-row lg:items-center lg:justify-between">
            <div>
              <h2 className="text-lg font-semibold">Suppliers</h2>
              <p className="text-sm text-slate-500">
                {loading ? 'Loading...' : `${totalSuppliers} suppliers found${startRow ? ` · showing ${startRow}-${endRow}` : ''}`}
              </p>
            </div>
            <div className="flex flex-wrap items-center gap-2">
              <label className="text-xs font-semibold uppercase tracking-[0.16em] text-slate-500" htmlFor="supplier-page-size">
                Rows
              </label>
              <select
                className="rounded-full border border-slate-200 bg-white px-3 py-2 text-sm font-semibold outline-none focus:border-brand"
                id="supplier-page-size"
                value={pageSize}
                onChange={(event) => {
                  setPage(1)
                  setPageSize(Number(event.target.value))
                }}
              >
                <option value={25}>25</option>
                <option value={50}>50</option>
                <option value={100}>100</option>
              </select>
              <div className="flex items-center rounded-full border border-slate-200 bg-slate-50 p-1">
                <button
                  className="rounded-full px-3 py-1.5 text-xs font-semibold text-slate-600 disabled:cursor-not-allowed disabled:opacity-40"
                  disabled={loading || page <= 1}
                  onClick={() => goToPage(1)}
                  type="button"
                >
                  First
                </button>
                <button
                  className="rounded-full px-3 py-1.5 text-xs font-semibold text-slate-600 disabled:cursor-not-allowed disabled:opacity-40"
                  disabled={loading || page <= 1}
                  onClick={() => goToPage(page - 1)}
                  type="button"
                >
                  Prev
                </button>
                <span className="px-3 py-1.5 text-xs font-semibold text-slate-500">
                  Page {page} / {totalPages}
                </span>
                <button
                  className="rounded-full px-3 py-1.5 text-xs font-semibold text-slate-600 disabled:cursor-not-allowed disabled:opacity-40"
                  disabled={loading || page >= totalPages}
                  onClick={() => goToPage(page + 1)}
                  type="button"
                >
                  Next
                </button>
                <button
                  className="rounded-full px-3 py-1.5 text-xs font-semibold text-slate-600 disabled:cursor-not-allowed disabled:opacity-40"
                  disabled={loading || page >= totalPages}
                  onClick={() => goToPage(totalPages)}
                  type="button"
                >
                  Last
                </button>
              </div>
            </div>
          </div>

          <div className="overflow-x-auto">
            <table className="min-w-full divide-y divide-slate-200 text-left text-sm">
              <thead className="bg-slate-50 text-xs uppercase tracking-[0.16em] text-slate-500">
                <tr>
                  <th className="px-5 py-4">Supplier</th>
                  <th className="px-5 py-4">Pilot</th>
                  <th className="px-5 py-4">AI Calls</th>
                  <th className="px-5 py-4">Quotes</th>
                  <th className="px-5 py-4">Orders</th>
                  <th className="px-5 py-4">Subscription</th>
                  <th className="px-5 py-4">Actions</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-100">
                {!loading && data.suppliers.length === 0 ? (
                  <tr>
                    <td className="px-5 py-12 text-center text-slate-500" colSpan={7}>
                      No suppliers found for these filters.
                    </td>
                  </tr>
                ) : null}
                {data.suppliers.map((supplier) => {
                  const supplierId = supplier.supplier_id
                  const links = generatedLinks[supplierId]
                  return (
                    <tr key={`${supplier.source_collection}:${supplierId}`} className="align-top">
                      <td className="px-5 py-4">
                        <a className="font-semibold text-slate-950 hover:text-brand" href={`/internal/suppliers/${encodeURIComponent(supplierId)}`}>
                          {supplier.company_name}
                        </a>
                        <p className="mt-1 text-xs text-slate-500">{supplier.city || 'City missing'} · {supplier.phone_number || 'No phone'}</p>
                        <div className="mt-2 flex max-w-md flex-wrap gap-1">
                          {(supplier.categories || []).slice(0, 3).map((category) => (
                            <span key={category} className="rounded-full bg-slate-100 px-2 py-1 text-[11px] font-medium text-slate-600">
                              {category}
                            </span>
                          ))}
                        </div>
                      </td>
                      <td className="px-5 py-4">
                        <span className={`inline-flex rounded-full border px-3 py-1 text-xs font-semibold ${statusBadge(supplier.pilotStatus)}`}>
                          {supplier.pilotStatus || 'Pending'}
                        </span>
                        <p className="mt-2 text-xs text-slate-500">{formatDate(supplier.pilotAcceptedAt)}</p>
                      </td>
                      <td className="px-5 py-4 font-semibold">{supplier.metrics?.aiCallsReceived || 0}</td>
                      <td className="px-5 py-4 font-semibold">{supplier.metrics?.quotesSubmitted || 0}</td>
                      <td className="px-5 py-4 font-semibold">{supplier.metrics?.ordersWon || 0}</td>
                      <td className="px-5 py-4">{supplier.subscriptionStatus || 'Pending'}</td>
                      <td className="px-5 py-4">
                        <div className="flex flex-wrap gap-2">
                          <button
                            className="rounded-full bg-slate-950 px-3 py-2 text-xs font-semibold text-white disabled:opacity-50"
                            disabled={busySupplier === supplierId}
                            onClick={() => generatePilotLink(supplierId)}
                            type="button"
                          >
                            {busySupplier === supplierId ? 'Generating...' : 'Generate pilot link'}
                          </button>
                          {links?.public_url ? (
                            <a className="rounded-full border border-slate-200 px-3 py-2 text-xs font-semibold text-slate-700" href={links.public_url}>
                              Public page
                            </a>
                          ) : null}
                        </div>
                        {links?.pilot_url ? (
                          <p className="mt-2 max-w-xs truncate text-xs text-emerald-700">Copied: {links.pilot_url}</p>
                        ) : null}
                      </td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>

          <div className="flex flex-col gap-3 border-t border-slate-200 px-5 py-4 sm:flex-row sm:items-center sm:justify-between">
            <p className="text-sm text-slate-500">
              {loading ? 'Refreshing suppliers...' : startRow ? `Showing ${startRow}-${endRow} of ${totalSuppliers}` : 'No suppliers to show'}
            </p>
            <div className="flex flex-wrap items-center gap-2">
              <button
                className="rounded-full border border-slate-200 px-4 py-2 text-sm font-semibold text-slate-700 disabled:cursor-not-allowed disabled:opacity-40"
                disabled={loading || page <= 1}
                onClick={() => goToPage(page - 1)}
                type="button"
              >
                Previous
              </button>
              <button
                className="rounded-full bg-slate-950 px-4 py-2 text-sm font-semibold text-white disabled:cursor-not-allowed disabled:opacity-40"
                disabled={loading || page >= totalPages}
                onClick={() => goToPage(page + 1)}
                type="button"
              >
                Next page
              </button>
            </div>
          </div>
        </section>
      </main>
    </div>
  )
}

export default SupplierCrmDashboard
