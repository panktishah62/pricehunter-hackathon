import { useEffect, useState } from 'react'
import { BRAND_LOGO_192_URL } from '../lib/brandAssets'
import LegalFooter from '../landing-page/LegalFooter'

const apiBaseUrl = import.meta.env.VITE_API_URL || 'http://localhost:8000'

function formatDate(value) {
  if (!value) return ''
  try {
    return new Intl.DateTimeFormat('en-IN', { dateStyle: 'medium', timeStyle: 'short' }).format(new Date(value))
  } catch {
    return ''
  }
}

function SupplierLoiPage({ token }) {
  const [state, setState] = useState({
    loading: true,
    error: '',
    agreement: null,
    signerName: '',
    agreed: false,
    submitting: false,
  })

  useEffect(() => {
    let cancelled = false
    async function loadAgreement() {
      if (!token) {
        setState((current) => ({ ...current, loading: false, error: 'Missing LOI token.' }))
        return
      }
      try {
        const response = await fetch(`${apiBaseUrl}/api/suppliers/loi/${encodeURIComponent(token)}`)
        if (!response.ok) {
          throw new Error(response.status === 404 ? 'This LOI link was not found.' : 'Could not load LOI.')
        }
        const payload = await response.json()
        if (!cancelled) {
          setState((current) => ({ ...current, loading: false, agreement: payload.agreement, error: '' }))
        }
      } catch (err) {
        if (!cancelled) {
          setState((current) => ({ ...current, loading: false, error: err.message || 'Could not load LOI.' }))
        }
      }
    }
    loadAgreement()
    return () => {
      cancelled = true
    }
  }, [token])

  async function acceptAgreement() {
    if (!state.agreed || !state.signerName.trim() || state.submitting) return
    setState((current) => ({ ...current, submitting: true, error: '' }))
    try {
      const response = await fetch(`${apiBaseUrl}/api/suppliers/loi/${encodeURIComponent(token)}/accept`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ agreed: true, signer_name: state.signerName.trim() }),
      })
      if (!response.ok) {
        const body = await response.json().catch(() => ({}))
        throw new Error(body.detail || 'Could not sign LOI.')
      }
      const payload = await response.json()
      setState((current) => ({
        ...current,
        submitting: false,
        agreement: payload.agreement,
        error: '',
      }))
      window.scrollTo({ top: 0, behavior: 'smooth' })
    } catch (err) {
      setState((current) => ({ ...current, submitting: false, error: err.message || 'Could not sign LOI.' }))
    }
  }

  const agreement = state.agreement
  const supplier = agreement?.supplier_snapshot || {}
  const signed = agreement?.status === 'Signed'

  return (
    <div className="min-h-screen bg-[#ecece6] text-slate-950">
      <header className="border-b border-slate-200 bg-[#fcfcf9]/90 backdrop-blur">
        <div className="mx-auto flex max-w-4xl items-center justify-between px-4 py-4 sm:px-6">
          <a href="/" className="inline-flex items-center gap-3" aria-label="ZWIG home">
            <img src={BRAND_LOGO_192_URL} alt="ZWIG" className="h-9 w-9 object-contain" />
            <div>
              <p className="text-sm font-semibold uppercase tracking-[0.24em] text-slate-500">ZWIG</p>
              <p className="hidden text-xs text-slate-500 sm:block">Supplier LOI</p>
            </div>
          </a>
          <span className="rounded-full border border-slate-200 bg-white px-3 py-1 text-xs font-semibold text-slate-700">
            {agreement?.agreement_version || 'Supplier pilot'}
          </span>
        </div>
      </header>

      <main className="mx-auto max-w-4xl px-4 py-8 sm:px-6 sm:py-12">
        {state.loading ? (
          <div className="rounded-[2rem] border border-slate-200 bg-[#fcfcf9] p-8 text-center text-slate-500">
            Loading LOI...
          </div>
        ) : null}

        {!state.loading && state.error && !agreement ? (
          <div className="rounded-[2rem] border border-rose-200 bg-rose-50 p-8 text-center text-rose-800">
            {state.error}
          </div>
        ) : null}

        {agreement ? (
          <div className="space-y-5">
            {signed ? (
              <section className="rounded-[2rem] border border-emerald-200 bg-[#fcfcf9] p-6 shadow-[0_20px_80px_rgba(15,23,42,0.06)] sm:p-8">
                <div className="inline-flex h-14 w-14 items-center justify-center rounded-full bg-emerald-100 text-2xl text-emerald-700">
                  ✓
                </div>
                <p className="mt-6 text-xs font-semibold uppercase tracking-[0.22em] text-emerald-600">LOI Signed</p>
                <h1 className="mt-3 text-3xl font-semibold tracking-[-0.03em] text-slate-950">
                  You're now a verified pilot supplier on ZWIG.
                </h1>
                <p className="mt-4 text-sm leading-6 text-slate-600">
                  Signed by {agreement.signed_by || 'supplier'} {agreement.signed_at ? `on ${formatDate(agreement.signed_at)}` : ''}.
                </p>
              </section>
            ) : null}

            <section className="rounded-[2rem] border border-slate-200 bg-[#fcfcf9] p-6 shadow-[0_20px_80px_rgba(15,23,42,0.06)] sm:p-8">
              <p className="text-xs font-semibold uppercase tracking-[0.22em] text-brand">Letter of Intent</p>
              <h1 className="mt-3 text-3xl font-semibold tracking-[-0.03em] sm:text-4xl">
                ZWIG Supplier Pilot Agreement
              </h1>
              <p className="mt-4 text-sm leading-6 text-slate-600">
                Please review the supplier details and pilot terms below. This acceptance is recorded digitally with
                timestamp, device information, and agreement hash.
              </p>

              <div className="mt-6 grid gap-3 sm:grid-cols-2">
                <div className="rounded-2xl border border-slate-200 bg-white p-4">
                  <p className="text-xs font-semibold uppercase tracking-[0.16em] text-slate-400">Supplier</p>
                  <p className="mt-2 font-semibold text-slate-950">{supplier.company_name || 'Supplier'}</p>
                </div>
                <div className="rounded-2xl border border-slate-200 bg-white p-4">
                  <p className="text-xs font-semibold uppercase tracking-[0.16em] text-slate-400">City</p>
                  <p className="mt-2 font-semibold text-slate-950">{supplier.city || 'Not available'}</p>
                </div>
                <div className="rounded-2xl border border-slate-200 bg-white p-4">
                  <p className="text-xs font-semibold uppercase tracking-[0.16em] text-slate-400">GST</p>
                  <p className="mt-2 font-semibold text-slate-950">{supplier.gst || 'Not available'}</p>
                </div>
                <div className="rounded-2xl border border-slate-200 bg-white p-4">
                  <p className="text-xs font-semibold uppercase tracking-[0.16em] text-slate-400">Agreement Hash</p>
                  <p className="mt-2 break-all font-mono text-xs text-slate-700">{agreement.agreement_hash}</p>
                </div>
              </div>

              <div className="mt-6 rounded-2xl border border-slate-200 bg-white p-5">
                <p className="text-xs font-semibold uppercase tracking-[0.16em] text-slate-400">Terms</p>
                <p className="mt-3 text-sm leading-7 text-slate-700">{agreement.agreement_text}</p>
              </div>

              {!signed ? (
                <div className="mt-6 space-y-4">
                  <label className="block">
                    <span className="text-sm font-semibold text-slate-700">Signer name</span>
                    <input
                      className="mt-2 w-full rounded-2xl border border-slate-200 px-4 py-3 text-sm outline-none focus:border-brand"
                      placeholder="Enter your full name"
                      value={state.signerName}
                      onChange={(event) => setState((current) => ({ ...current, signerName: event.target.value }))}
                    />
                  </label>
                  <label className="flex items-start gap-3 rounded-2xl border border-slate-200 bg-white p-4">
                    <input
                      checked={state.agreed}
                      onChange={(event) => setState((current) => ({ ...current, agreed: event.target.checked }))}
                      className="mt-1 h-5 w-5 rounded border-slate-300 text-brand"
                      type="checkbox"
                    />
                    <span className="text-sm font-medium leading-6 text-slate-800">
                      I have read and agree to the ZWIG Supplier Pilot LOI terms.
                    </span>
                  </label>
                  {state.error ? (
                    <p className="rounded-xl border border-rose-200 bg-rose-50 px-4 py-3 text-sm text-rose-800">
                      {state.error}
                    </p>
                  ) : null}
                  <button
                    className="w-full rounded-full bg-brand px-6 py-4 text-sm font-semibold text-white transition hover:bg-brand-deep disabled:cursor-not-allowed disabled:bg-slate-300 sm:w-auto"
                    disabled={!state.agreed || !state.signerName.trim() || state.submitting}
                    onClick={acceptAgreement}
                    type="button"
                  >
                    {state.submitting ? 'Signing...' : 'I agree and sign'}
                  </button>
                </div>
              ) : null}
            </section>
          </div>
        ) : null}
      </main>

      <LegalFooter />
    </div>
  )
}

export default SupplierLoiPage
