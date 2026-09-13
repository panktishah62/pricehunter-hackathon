import { useEffect, useState } from 'react'
import { BRAND_LOGO_192_URL } from '../lib/brandAssets'
import LegalFooter from '../landing-page/LegalFooter'

const apiBaseUrl = import.meta.env.VITE_API_URL || 'http://localhost:8000'

function formatPrice(product) {
  if (product.price === null || product.price === undefined || product.price === '') return 'Ask for price'
  return `${product.currency || 'INR'} ${product.price}${product.unit ? ` / ${product.unit}` : ''}`
}

function productImage(product) {
  return product?.image_url || product?.images?.[0] || product?.source_image_url || ''
}

function SupplierPublicPage({ slug }) {
  const [state, setState] = useState({ loading: true, error: '', payload: null })

  useEffect(() => {
    let cancelled = false
    async function loadProfile() {
      try {
        const response = await fetch(`${apiBaseUrl}/api/suppliers/public/${encodeURIComponent(slug || '')}`)
        if (!response.ok) throw new Error(response.status === 404 ? 'Supplier profile not found.' : 'Could not load supplier profile.')
        const payload = await response.json()
        if (!cancelled) setState({ loading: false, error: '', payload })
      } catch (err) {
        if (!cancelled) setState({ loading: false, error: err.message || 'Could not load supplier profile.', payload: null })
      }
    }
    loadProfile()
    return () => {
      cancelled = true
    }
  }, [slug])

  const supplier = state.payload?.supplier
  const products = state.payload?.products || []
  const verified = Boolean(supplier?.pilotAccepted || supplier?.pilotStatus === 'Active')

  return (
    <div className="min-h-screen bg-[#f7f3ea] text-slate-950">
      <header className="border-b border-slate-200 bg-white">
        <div className="mx-auto flex max-w-7xl items-center justify-between px-4 py-4 sm:px-6">
          <a href="/" className="inline-flex items-center gap-3">
            <img src={BRAND_LOGO_192_URL} alt="ZWIG" className="h-9 w-9 object-contain" />
            <div>
              <p className="text-sm font-semibold uppercase tracking-[0.24em] text-slate-500">ZWIG</p>
              <p className="hidden text-xs text-slate-500 sm:block">Supplier Network</p>
            </div>
          </a>
          <a className="rounded-full bg-slate-950 px-4 py-2 text-sm font-semibold text-white" href="/#/app">
            Request quote
          </a>
        </div>
      </header>

      <main>
        {state.loading ? (
          <div className="mx-auto max-w-4xl px-4 py-20 text-center text-slate-500">Loading supplier profile...</div>
        ) : null}
        {state.error ? (
          <div className="mx-auto max-w-4xl px-4 py-20">
            <div className="rounded-[2rem] border border-rose-200 bg-rose-50 p-8 text-center text-rose-800">{state.error}</div>
          </div>
        ) : null}

        {supplier ? (
          <>
            <section className="bg-[#11251f] text-white">
              <div className="mx-auto grid max-w-7xl gap-8 px-4 py-12 sm:px-6 lg:grid-cols-[1.2fr_0.8fr] lg:py-16">
                <div>
                  <p className="text-xs font-semibold uppercase tracking-[0.28em] text-emerald-200">Supplier Profile</p>
                  <h1 className="mt-4 text-4xl font-semibold tracking-[-0.05em] sm:text-6xl">{supplier.company_name}</h1>
                  <p className="mt-5 max-w-2xl text-base leading-7 text-emerald-50/80">
                    {supplier.address || supplier.city
                      ? `${supplier.address || supplier.city}`
                      : 'Supplier details will appear here once the ZWIG team verifies the profile.'}
                  </p>
                  <div className="mt-6 flex flex-wrap gap-2">
                    {verified ? (
                      <span className="rounded-full bg-emerald-400 px-4 py-2 text-sm font-semibold text-emerald-950">Verified Pilot Supplier</span>
                    ) : (
                      <span className="rounded-full bg-white/10 px-4 py-2 text-sm font-semibold text-white">Profile in progress</span>
                    )}
                    {supplier.city ? <span className="rounded-full bg-white/10 px-4 py-2 text-sm font-semibold text-white">{supplier.city}</span> : null}
                  </div>
                </div>
                <div className="rounded-[2rem] border border-white/10 bg-white/10 p-5 backdrop-blur">
                  <p className="text-xs font-semibold uppercase tracking-[0.22em] text-emerald-100">Company Snapshot</p>
                  <dl className="mt-5 space-y-4 text-sm">
                    <div>
                      <dt className="text-emerald-100/70">GST</dt>
                      <dd className="mt-1 font-semibold">{supplier.verification?.gst || 'Not available'}</dd>
                    </div>
                    <div>
                      <dt className="text-emerald-100/70">Business Type</dt>
                      <dd className="mt-1 font-semibold">{supplier.business_details?.business_type || 'Not available'}</dd>
                    </div>
                    <div>
                      <dt className="text-emerald-100/70">Turnover</dt>
                      <dd className="mt-1 font-semibold">{supplier.business_details?.turnover || 'Not available'}</dd>
                    </div>
                    <div>
                      <dt className="text-emerald-100/70">Website</dt>
                      <dd className="mt-1 font-semibold">{supplier.website || 'Not available'}</dd>
                    </div>
                  </dl>
                </div>
              </div>
            </section>

            <section className="mx-auto max-w-7xl px-4 py-8 sm:px-6">
              <div className="rounded-[2rem] border border-slate-200 bg-white p-5 shadow-[0_20px_80px_rgba(15,23,42,0.05)]">
                <div className="flex flex-col gap-3 sm:flex-row sm:items-end sm:justify-between">
                  <div>
                    <p className="text-xs font-semibold uppercase tracking-[0.22em] text-brand">Catalog</p>
                    <h2 className="mt-2 text-2xl font-semibold tracking-[-0.03em]">Products and offerings</h2>
                  </div>
                  <p className="text-sm text-slate-500">{products.length} products listed</p>
                </div>

                <div className="mt-6 grid gap-4 md:grid-cols-2 xl:grid-cols-3">
                  {products.length ? (
                    products.map((product) => (
                      <article key={product.offering_id || product.product_name} className="overflow-hidden rounded-[1.5rem] border border-slate-200 bg-[#fcfcf9]">
                        {productImage(product) ? (
                          <img
                            alt={product.product_name}
                            className="h-44 w-full bg-white object-contain p-4"
                            loading="lazy"
                            src={productImage(product)}
                          />
                        ) : (
                          <div className="flex h-44 items-center justify-center bg-gradient-to-br from-slate-100 to-amber-50 text-sm font-semibold text-slate-400">
                            Product image
                          </div>
                        )}
                        <div className="p-4">
                          <p className="font-semibold text-slate-950">{product.product_name}</p>
                          <p className="mt-1 text-sm text-slate-500">{product.category || 'Catalog item'}</p>
                          <p className="mt-4 text-lg font-semibold text-slate-950">{formatPrice(product)}</p>
                          <button className="mt-4 w-full rounded-full bg-slate-950 px-4 py-3 text-sm font-semibold text-white" type="button">
                            Request quote
                          </button>
                        </div>
                      </article>
                    ))
                  ) : (
                    <div className="rounded-[1.5rem] border border-dashed border-slate-300 bg-slate-50 p-8 text-center text-sm text-slate-500 md:col-span-2 xl:col-span-3">
                      Products will appear here once this supplier catalog is verified by ZWIG.
                    </div>
                  )}
                </div>
              </div>
            </section>
          </>
        ) : null}
      </main>

      <LegalFooter />
    </div>
  )
}

export default SupplierPublicPage
