import { useEffect, useMemo, useState } from 'react'
import { BRAND_LOGO_192_URL } from '../lib/brandAssets'
import LegalFooter from '../landing-page/LegalFooter'

const apiBaseUrl = import.meta.env.VITE_API_URL || 'http://localhost:8000'

function formatDate(value) {
  if (!value) return ''
  try {
    return new Intl.DateTimeFormat('en-IN', {
      dateStyle: 'medium',
      timeStyle: 'short',
    }).format(new Date(value))
  } catch {
    return ''
  }
}

function PilotHeader() {
  return (
    <header className="border-b border-slate-200 bg-[#fcfcf9]/90 backdrop-blur">
      <div className="mx-auto flex max-w-4xl items-center justify-between px-4 py-4 sm:px-6">
        <a href="/" className="inline-flex items-center gap-3" aria-label="ZWIG home">
          <img src={BRAND_LOGO_192_URL} alt="ZWIG" className="h-9 w-9 object-contain" />
          <div>
            <p className="text-sm font-semibold uppercase tracking-[0.24em] text-slate-500">ZWIG</p>
            <p className="hidden text-xs text-slate-500 sm:block">Supplier Pilot</p>
          </div>
        </a>
        <span className="rounded-full border border-emerald-200 bg-emerald-50 px-3 py-1 text-xs font-semibold text-emerald-700">
          Free pilot
        </span>
      </div>
    </header>
  )
}

function DetailRow({ label, value }) {
  return (
    <div className="rounded-2xl border border-slate-200 bg-white p-4">
      <dt className="text-xs font-semibold uppercase tracking-[0.16em] text-slate-400">{label}</dt>
      <dd className="mt-2 text-base font-semibold text-slate-950">{value || 'Not available'}</dd>
    </div>
  )
}

function LoadingState() {
  return (
    <div className="rounded-[2rem] border border-slate-200 bg-[#fcfcf9] p-6 text-center shadow-[0_20px_80px_rgba(15,23,42,0.06)]">
      <div className="mx-auto h-10 w-10 animate-spin rounded-full border-2 border-slate-200 border-t-brand" />
      <p className="mt-4 text-sm font-medium text-slate-600">Loading supplier pilot details...</p>
    </div>
  )
}

function ErrorState({ message }) {
  return (
    <div className="rounded-[2rem] border border-rose-200 bg-rose-50 p-6 text-center shadow-[0_20px_80px_rgba(15,23,42,0.06)]">
      <p className="text-sm font-semibold uppercase tracking-[0.18em] text-rose-500">Link unavailable</p>
      <h1 className="mt-3 text-2xl font-semibold text-rose-950">We could not open this supplier pilot link.</h1>
      <p className="mt-3 text-sm leading-6 text-rose-800">
        {message || 'Please ask your ZWIG contact to resend the onboarding link.'}
      </p>
    </div>
  )
}

function Confirmation({ supplier, acceptedAt }) {
  return (
    <div className="rounded-[2rem] border border-emerald-200 bg-[#fcfcf9] p-6 shadow-[0_20px_80px_rgba(15,23,42,0.06)] sm:p-8">
      <div className="inline-flex h-14 w-14 items-center justify-center rounded-full bg-emerald-100 text-2xl text-emerald-700">
        ✓
      </div>
      <p className="mt-6 text-xs font-semibold uppercase tracking-[0.22em] text-emerald-600">Pilot Active</p>
      <h1 className="mt-3 text-3xl font-semibold tracking-[-0.02em] text-slate-950 sm:text-4xl">
        You're now part of the ZWIG Procurement Network.
      </h1>
      <p className="mt-4 text-base leading-7 text-slate-600">
        Your supplier profile for <strong className="text-slate-950">{supplier?.company_name || 'your company'}</strong> is now active.
        We'll notify you whenever a verified procurement enquiry matches your products.
      </p>
      <div className="mt-6 rounded-2xl border border-emerald-200 bg-emerald-50 p-4">
        <p className="text-sm font-semibold text-emerald-900">Status: Pilot Active</p>
        {acceptedAt ? <p className="mt-1 text-xs text-emerald-700">Accepted on {formatDate(acceptedAt)}</p> : null}
      </div>
    </div>
  )
}

function SupplierPilotPage({ token }) {
  const [state, setState] = useState({
    loading: true,
    error: '',
    payload: null,
    agreed: false,
    submitting: false,
  })

  useEffect(() => {
    let cancelled = false

    async function loadPilot() {
      if (!token) {
        setState((current) => ({ ...current, loading: false, error: 'Missing supplier pilot token.' }))
        return
      }
      try {
        const response = await fetch(`${apiBaseUrl}/api/supplier/pilot/${encodeURIComponent(token)}`)
        if (!response.ok) {
          throw new Error(response.status === 404 ? 'This supplier pilot link was not found.' : 'Could not load supplier details.')
        }
        const payload = await response.json()
        if (!cancelled) {
          setState((current) => ({ ...current, loading: false, payload, error: '' }))
        }
      } catch (error) {
        if (!cancelled) {
          setState((current) => ({ ...current, loading: false, error: error.message || 'Could not load supplier details.' }))
        }
      }
    }

    loadPilot()
    return () => {
      cancelled = true
    }
  }, [token])

  const supplier = state.payload?.supplier
  const categories = useMemo(() => supplier?.categories?.filter(Boolean) || [], [supplier])
  const accepted = Boolean(state.payload?.pilotAccepted || state.payload?.pilotStatus === 'Active')

  async function acceptPilot() {
    if (!state.agreed || state.submitting) return
    setState((current) => ({ ...current, submitting: true, error: '' }))
    try {
      const response = await fetch(`${apiBaseUrl}/api/supplier/pilot/${encodeURIComponent(token)}/accept`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ agreed: true }),
      })
      if (!response.ok) {
        const body = await response.json().catch(() => ({}))
        throw new Error(body.detail || 'Could not activate supplier pilot.')
      }
      const payload = await response.json()
      setState((current) => ({ ...current, submitting: false, payload, agreed: true, error: '' }))
      window.scrollTo({ top: 0, behavior: 'smooth' })
    } catch (error) {
      setState((current) => ({
        ...current,
        submitting: false,
        error: error.message || 'Could not activate supplier pilot.',
      }))
    }
  }

  return (
    <div className="min-h-screen bg-[#ecece6] text-slate-950">
      <PilotHeader />

      <main className="mx-auto max-w-4xl px-4 py-8 sm:px-6 sm:py-12">
        {state.loading ? <LoadingState /> : null}
        {!state.loading && state.error && !state.payload ? <ErrorState message={state.error} /> : null}
        {!state.loading && accepted ? <Confirmation supplier={supplier} acceptedAt={state.payload?.pilotAcceptedAt} /> : null}

        {!state.loading && supplier && !accepted ? (
          <div className="space-y-5">
            <section className="rounded-[2rem] border border-slate-200 bg-[#fcfcf9] p-6 shadow-[0_20px_80px_rgba(15,23,42,0.06)] sm:p-8">
              <p className="text-xs font-semibold uppercase tracking-[0.22em] text-brand">Supplier Pilot</p>
              <h1 className="mt-3 text-3xl font-semibold tracking-[-0.02em] sm:text-4xl">
                Welcome to the ZWIG Verified Supplier Network
              </h1>
              <p className="mt-4 text-base leading-7 text-slate-600">
                You're joining the ZWIG Procurement Network. Please review your supplier details below and activate the pilot.
              </p>
            </section>

            <section className="rounded-[2rem] border border-slate-200 bg-[#fcfcf9] p-5 sm:p-6">
              <h2 className="text-lg font-semibold text-slate-950">Supplier details</h2>
              <p className="mt-2 text-sm leading-6 text-slate-500">
                These details are already captured by ZWIG and are shown as read-only.
              </p>
              <dl className="mt-5 grid gap-3 sm:grid-cols-2">
                <DetailRow label="Company Name" value={supplier.company_name} />
                <DetailRow label="Contact Person" value={supplier.contact_person} />
                <DetailRow label="Phone Number" value={supplier.phone_number} />
                <DetailRow label="GST" value={supplier.gst} />
                <DetailRow label="City" value={supplier.city} />
                <DetailRow label="Supplier ID" value={supplier.supplier_id} />
              </dl>
              <div className="mt-3 rounded-2xl border border-slate-200 bg-white p-4">
                <p className="text-xs font-semibold uppercase tracking-[0.16em] text-slate-400">Product Categories</p>
                {categories.length ? (
                  <div className="mt-3 flex flex-wrap gap-2">
                    {categories.map((category) => (
                      <span key={category} className="rounded-full bg-slate-100 px-3 py-1 text-sm font-medium text-slate-700">
                        {category}
                      </span>
                    ))}
                  </div>
                ) : (
                  <p className="mt-2 text-sm text-slate-600">Not available</p>
                )}
              </div>
            </section>

            <section className="rounded-[2rem] border border-slate-200 bg-[#fcfcf9] p-6 sm:p-8">
              <h2 className="text-2xl font-semibold tracking-[-0.02em] text-slate-950">
                Join the ZWIG Supplier Pilot
              </h2>
              <p className="mt-4 text-sm leading-7 text-slate-600">As a participating supplier:</p>
              <ul className="mt-4 space-y-3 text-sm leading-6 text-slate-700">
                <li>• You'll receive verified procurement enquiries from enterprise buyers.</li>
                <li>• You can decide which enquiries to respond to.</li>
                <li>• Participation in this pilot is completely free.</li>
                <li>• There are no upfront charges or lock-ins.</li>
                <li>
                  • If ZWIG consistently generates meaningful business opportunities for your company, you agree to
                  discuss a commercial partnership with ZWIG.
                </li>
              </ul>
              <p className="mt-5 rounded-2xl bg-slate-100 p-4 text-sm leading-6 text-slate-700">
                By continuing, you confirm that you agree to participate in the ZWIG Supplier Pilot.
              </p>

              <label className="mt-5 flex items-start gap-3 rounded-2xl border border-slate-200 bg-white p-4">
                <input
                  checked={state.agreed}
                  onChange={(event) => setState((current) => ({ ...current, agreed: event.target.checked }))}
                  className="mt-1 h-5 w-5 rounded border-slate-300 text-brand"
                  type="checkbox"
                />
                <span className="text-sm font-medium leading-6 text-slate-800">I have read and agree.</span>
              </label>

              {state.error ? (
                <p className="mt-4 rounded-xl border border-rose-200 bg-rose-50 px-4 py-3 text-sm text-rose-800">
                  {state.error}
                </p>
              ) : null}

              <button
                className="mt-5 w-full rounded-full bg-brand px-6 py-4 text-sm font-semibold text-white transition hover:bg-brand-deep disabled:cursor-not-allowed disabled:bg-slate-300 sm:w-auto"
                type="button"
                disabled={!state.agreed || state.submitting}
                onClick={acceptPilot}
              >
                {state.submitting ? 'Activating...' : 'Join Pilot'}
              </button>
              <p className="mt-3 text-xs leading-5 text-slate-500">
                Agreement version: {state.payload?.agreementVersion || state.payload?.pilotAgreementVersion}
              </p>
            </section>
          </div>
        ) : null}
      </main>

      <LegalFooter />
    </div>
  )
}

export default SupplierPilotPage
