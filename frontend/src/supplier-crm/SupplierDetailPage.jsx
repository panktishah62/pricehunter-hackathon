import { useEffect, useMemo, useState } from 'react'

const apiBaseUrl = import.meta.env.VITE_API_URL || 'http://localhost:8000'

function formatDate(value) {
  if (!value) return '—'
  try {
    return new Intl.DateTimeFormat('en-IN', { dateStyle: 'medium', timeStyle: 'short' }).format(new Date(value))
  } catch {
    return '—'
  }
}

function formatPrice(product) {
  if (product.price === null || product.price === undefined || product.price === '') return 'Price not available'
  return `${product.currency || 'INR'} ${product.price}${product.unit ? ` / ${product.unit}` : ''}`
}

function productImage(product) {
  return product?.image_url || product?.images?.[0] || product?.source_image_url || ''
}

function InfoCard({ label, value }) {
  return (
    <div className="rounded-2xl border border-slate-200 bg-white p-4">
      <p className="text-xs font-semibold uppercase tracking-[0.18em] text-slate-400">{label}</p>
      <p className="mt-2 text-sm font-semibold text-slate-950">{value || 'Not available'}</p>
    </div>
  )
}

function SupplierDetailPage({ supplierId }) {
  const [state, setState] = useState({ loading: true, error: '', payload: null })
  const [uploading, setUploading] = useState(false)
  const [busyAction, setBusyAction] = useState('')
  const [documentType, setDocumentType] = useState('catalog')
  const [pilotLink, setPilotLink] = useState(null)
  const [loiLink, setLoiLink] = useState(null)

  const encodedSupplierId = useMemo(() => encodeURIComponent(supplierId || ''), [supplierId])

  async function loadSupplier() {
    if (!supplierId) {
      setState({ loading: false, error: 'Missing supplier id.', payload: null })
      return
    }
    setState((current) => ({ ...current, loading: true, error: '' }))
    try {
      const response = await fetch(`${apiBaseUrl}/api/suppliers/crm/${encodedSupplierId}`)
      if (!response.ok) throw new Error('Could not load supplier.')
      const payload = await response.json()
      setState({ loading: false, error: '', payload })
    } catch (err) {
      setState({ loading: false, error: err.message || 'Could not load supplier.', payload: null })
    }
  }

  useEffect(() => {
    loadSupplier()
  }, [encodedSupplierId])

  async function generatePilotLink() {
    const response = await fetch(`${apiBaseUrl}/api/suppliers/crm/${encodedSupplierId}/pilot-link`, { method: 'POST' })
    if (!response.ok) return
    const payload = await response.json()
    setPilotLink(payload)
    navigator.clipboard?.writeText(payload.pilot_url).catch(() => {})
  }

  async function generateLoi() {
    setBusyAction('loi')
    try {
      const response = await fetch(`${apiBaseUrl}/api/suppliers/crm/${encodedSupplierId}/loi`, { method: 'POST' })
      if (!response.ok) throw new Error('Could not generate LOI.')
      const payload = await response.json()
      setLoiLink(payload)
      navigator.clipboard?.writeText(payload.loi_url).catch(() => {})
      await loadSupplier()
    } catch (err) {
      setState((current) => ({ ...current, error: err.message || 'Could not generate LOI.' }))
    } finally {
      setBusyAction('')
    }
  }

  async function extractDocument(documentId) {
    setBusyAction(`extract:${documentId}`)
    try {
      const response = await fetch(`${apiBaseUrl}/api/suppliers/crm/${encodedSupplierId}/documents/${encodeURIComponent(documentId)}/extract`, {
        method: 'POST',
      })
      if (!response.ok) throw new Error('Could not extract document.')
      await loadSupplier()
    } catch (err) {
      setState((current) => ({ ...current, error: err.message || 'Could not extract document.' }))
    } finally {
      setBusyAction('')
    }
  }

  async function applyExtraction(documentId) {
    setBusyAction(`apply:${documentId}`)
    try {
      const response = await fetch(`${apiBaseUrl}/api/suppliers/crm/${encodedSupplierId}/documents/${encodeURIComponent(documentId)}/apply`, {
        method: 'POST',
      })
      if (!response.ok) throw new Error('Could not apply extraction.')
      await loadSupplier()
    } catch (err) {
      setState((current) => ({ ...current, error: err.message || 'Could not apply extraction.' }))
    } finally {
      setBusyAction('')
    }
  }

  async function uploadDocuments(event) {
    event.preventDefault()
    const files = Array.from(event.currentTarget.elements.files.files || [])
    if (!files.length || uploading) return
    const form = new FormData()
    form.append('document_type', documentType)
    files.forEach((file) => form.append('files', file))
    setUploading(true)
    try {
      const response = await fetch(`${apiBaseUrl}/api/suppliers/crm/${encodedSupplierId}/documents`, {
        method: 'POST',
        body: form,
      })
      if (!response.ok) {
        const body = await response.json().catch(() => ({}))
        throw new Error(body.detail || 'Could not upload documents.')
      }
      await loadSupplier()
      event.currentTarget.reset()
    } catch (err) {
      setState((current) => ({ ...current, error: err.message || 'Could not upload documents.' }))
    } finally {
      setUploading(false)
    }
  }

  const supplier = state.payload?.supplier
  const products = state.payload?.products || []
  const documents = state.payload?.documents || []
  const lois = state.payload?.lois || []

  return (
    <div className="min-h-screen bg-[#f7f3ea] text-slate-950">
      <header className="border-b border-slate-200 bg-white/85 backdrop-blur">
        <div className="mx-auto max-w-7xl px-4 py-5 sm:px-6">
          <a className="text-sm font-semibold text-slate-500 hover:text-brand" href="/internal/suppliers">
            ← Supplier CRM
          </a>
          <div className="mt-4 flex flex-col gap-4 lg:flex-row lg:items-end lg:justify-between">
            <div>
              <p className="text-xs font-semibold uppercase tracking-[0.24em] text-brand">Supplier Workspace</p>
              <h1 className="mt-2 text-3xl font-semibold tracking-[-0.04em] sm:text-4xl">
                {supplier?.company_name || 'Supplier'}
              </h1>
              <p className="mt-2 text-sm text-slate-600">{supplier?.city || 'City missing'} · {supplier?.phone_number || 'No phone number'}</p>
            </div>
            {supplier ? (
              <div className="flex flex-wrap gap-2">
                <button className="rounded-full bg-slate-950 px-5 py-3 text-sm font-semibold text-white" onClick={generatePilotLink} type="button">
                  Generate pilot link
                </button>
                <button
                  className="rounded-full bg-brand px-5 py-3 text-sm font-semibold text-white disabled:opacity-50"
                  disabled={busyAction === 'loi'}
                  onClick={generateLoi}
                  type="button"
                >
                  {busyAction === 'loi' ? 'Generating LOI...' : 'Generate LOI'}
                </button>
                {supplier.publicSlug ? (
                  <a className="rounded-full border border-slate-300 bg-white px-5 py-3 text-sm font-semibold text-slate-800" href={`/suppliers/${supplier.publicSlug}`}>
                    Open public page
                  </a>
                ) : null}
              </div>
            ) : null}
          </div>
          {pilotLink?.pilot_url ? (
            <div className="mt-4 rounded-2xl border border-emerald-200 bg-emerald-50 p-4 text-sm text-emerald-800">
              Pilot link copied: <span className="font-semibold">{pilotLink.pilot_url}</span>
            </div>
          ) : null}
          {loiLink?.loi_url ? (
            <div className="mt-4 rounded-2xl border border-blue-200 bg-blue-50 p-4 text-sm text-blue-800">
              LOI link copied: <span className="font-semibold">{loiLink.loi_url}</span>
            </div>
          ) : null}
        </div>
      </header>

      <main className="mx-auto max-w-7xl px-4 py-6 sm:px-6">
        {state.loading ? <div className="rounded-3xl bg-white p-8 text-center text-slate-500">Loading supplier...</div> : null}
        {state.error ? <div className="mb-5 rounded-2xl border border-rose-200 bg-rose-50 p-4 text-sm text-rose-700">{state.error}</div> : null}

        {supplier ? (
          <div className="grid gap-5 lg:grid-cols-[0.9fr_1.4fr]">
            <div className="space-y-5">
              <section className="rounded-[2rem] border border-slate-200 bg-white p-5 shadow-[0_20px_80px_rgba(15,23,42,0.05)]">
                <h2 className="text-lg font-semibold">Company details</h2>
                <div className="mt-4 grid gap-3">
                  <InfoCard label="Contact Person" value={supplier.contact_person} />
                  <InfoCard label="GST" value={supplier.gst} />
                  <InfoCard label="Address" value={supplier.address} />
                  <InfoCard label="Website" value={supplier.website} />
                  <InfoCard label="Pilot Status" value={supplier.pilotStatus} />
                  <InfoCard label="Subscription" value={supplier.subscriptionStatus} />
                </div>
              </section>

              <section className="rounded-[2rem] border border-slate-200 bg-white p-5 shadow-[0_20px_80px_rgba(15,23,42,0.05)]">
                <h2 className="text-lg font-semibold">Upload supplier documents</h2>
                <p className="mt-2 text-sm leading-6 text-slate-500">
                  Drop catalogs, rate cards, GST certificates, company profiles, or LOI files here. Extraction is queued for review.
                </p>
                <form className="mt-4 space-y-3" onSubmit={uploadDocuments}>
                  <select
                    className="w-full rounded-2xl border border-slate-200 px-4 py-3 text-sm"
                    value={documentType}
                    onChange={(event) => setDocumentType(event.target.value)}
                  >
                    <option value="catalog">Catalog</option>
                    <option value="rate_card">Rate card</option>
                    <option value="gst_certificate">GST certificate</option>
                    <option value="company_profile">Company profile</option>
                    <option value="loi">LOI / agreement</option>
                    <option value="other">Other</option>
                  </select>
                  <input className="w-full rounded-2xl border border-dashed border-slate-300 bg-slate-50 p-4 text-sm" name="files" type="file" multiple />
                  <button className="w-full rounded-full bg-brand px-5 py-3 text-sm font-semibold text-white disabled:opacity-50" disabled={uploading} type="submit">
                    {uploading ? 'Uploading...' : 'Upload documents'}
                  </button>
                </form>
              </section>

              <section className="rounded-[2rem] border border-slate-200 bg-white p-5 shadow-[0_20px_80px_rgba(15,23,42,0.05)]">
                <h2 className="text-lg font-semibold">LOI agreements</h2>
                <div className="mt-4 divide-y divide-slate-100 rounded-2xl border border-slate-200">
                  {lois.length ? (
                    lois.map((loi) => (
                      <div key={loi.agreement_id} className="p-4">
                        <div className="flex items-center justify-between gap-3">
                          <p className="font-semibold text-slate-950">{loi.agreement_version}</p>
                          <span className="rounded-full bg-slate-100 px-3 py-1 text-xs font-semibold text-slate-700">{loi.status}</span>
                        </div>
                        <p className="mt-1 text-xs text-slate-500">Created {formatDate(loi.created_at)} · Signed {formatDate(loi.signed_at)}</p>
                      </div>
                    ))
                  ) : (
                    <div className="p-6 text-center text-sm text-slate-500">No LOI generated yet.</div>
                  )}
                </div>
              </section>
            </div>

            <div className="space-y-5">
              <section className="rounded-[2rem] border border-slate-200 bg-white p-5 shadow-[0_20px_80px_rgba(15,23,42,0.05)]">
                <div className="flex items-center justify-between">
                  <h2 className="text-lg font-semibold">Products and offerings</h2>
                  <span className="rounded-full bg-slate-100 px-3 py-1 text-xs font-semibold text-slate-600">{products.length} listed</span>
                </div>
                <div className="mt-4 grid gap-3 md:grid-cols-2">
                  {products.length ? (
                    products.map((product) => (
                      <article key={product.offering_id || product.product_name} className="overflow-hidden rounded-2xl border border-slate-200 bg-[#fcfcf9]">
                        {productImage(product) ? (
                          <img
                            alt={product.product_name}
                            className="h-36 w-full border-b border-slate-100 bg-white object-contain p-3"
                            loading="lazy"
                            src={productImage(product)}
                          />
                        ) : (
                          <div className="flex h-36 items-center justify-center border-b border-slate-100 bg-gradient-to-br from-slate-50 to-amber-50 text-xs font-semibold text-slate-400">
                            Product image
                          </div>
                        )}
                        <div className="p-4">
                          <p className="font-semibold text-slate-950">{product.product_name}</p>
                          <p className="mt-1 text-sm text-slate-600">{product.category || 'Uncategorized'}{product.subcategory ? ` · ${product.subcategory}` : ''}</p>
                          <p className="mt-3 text-sm font-semibold text-slate-950">{formatPrice(product)}</p>
                          {product.source_url ? (
                            <a className="mt-3 inline-flex text-xs font-semibold text-brand" href={product.source_url} target="_blank" rel="noreferrer">
                              View source link
                            </a>
                          ) : null}
                        </div>
                      </article>
                    ))
                  ) : (
                    <div className="rounded-2xl border border-dashed border-slate-300 p-8 text-center text-sm text-slate-500 md:col-span-2">
                      No products are available yet. Upload a catalog or rate card to build this supplier profile.
                    </div>
                  )}
                </div>
              </section>

              <section className="rounded-[2rem] border border-slate-200 bg-white p-5 shadow-[0_20px_80px_rgba(15,23,42,0.05)]">
                <h2 className="text-lg font-semibold">Documents</h2>
                <div className="mt-4 divide-y divide-slate-100 rounded-2xl border border-slate-200">
                  {documents.length ? (
                    documents.map((doc) => (
                      <div key={doc.document_id} className="p-4">
                        <div className="flex items-start justify-between gap-4">
                          <div>
                            <p className="font-semibold text-slate-950">{doc.original_filename}</p>
                            <p className="text-xs text-slate-500">{doc.document_type} · {doc.extraction_status} · {formatDate(doc.uploaded_at)}</p>
                          </div>
                          <span className="rounded-full bg-amber-50 px-3 py-1 text-xs font-semibold text-amber-700">{doc.review_status}</span>
                        </div>
                        {doc.extraction?.summary ? (
                          <div className="mt-3 rounded-2xl bg-slate-50 p-3 text-xs text-slate-600">
                            <p className="font-semibold text-slate-800">
                              {doc.extraction.summary.products_detected || 0} products detected
                              {doc.extraction.summary.has_prices ? ' · prices found' : ''}
                            </p>
                            {doc.extraction.warnings?.length ? <p className="mt-1 text-amber-700">{doc.extraction.warnings[0]}</p> : null}
                          </div>
                        ) : null}
                        <div className="mt-3 flex flex-wrap gap-2">
                          <button
                            className="rounded-full border border-slate-200 px-3 py-2 text-xs font-semibold text-slate-700 disabled:opacity-50"
                            disabled={busyAction === `extract:${doc.document_id}`}
                            onClick={() => extractDocument(doc.document_id)}
                            type="button"
                          >
                            {busyAction === `extract:${doc.document_id}` ? 'Extracting...' : 'Extract'}
                          </button>
                          <button
                            className="rounded-full bg-slate-950 px-3 py-2 text-xs font-semibold text-white disabled:opacity-50"
                            disabled={!doc.extraction?.product_candidates?.length || doc.review_status === 'applied' || busyAction === `apply:${doc.document_id}`}
                            onClick={() => applyExtraction(doc.document_id)}
                            type="button"
                          >
                            {busyAction === `apply:${doc.document_id}` ? 'Applying...' : 'Apply to DB'}
                          </button>
                        </div>
                        {doc.extraction?.product_candidates?.length ? (
                          <div className="mt-3 max-h-44 overflow-auto rounded-2xl border border-slate-100">
                            {doc.extraction.product_candidates.slice(0, 8).map((candidate, index) => (
                              <div key={`${candidate.product_name}-${index}`} className="flex justify-between gap-4 border-b border-slate-100 px-3 py-2 text-xs last:border-b-0">
                                <span className="font-medium text-slate-800">{candidate.product_name}</span>
                                <span className="text-slate-500">{candidate.price ? `₹${candidate.price}` : 'No price'}</span>
                              </div>
                            ))}
                          </div>
                        ) : null}
                      </div>
                    ))
                  ) : (
                    <div className="p-8 text-center text-sm text-slate-500">No documents uploaded yet.</div>
                  )}
                </div>
              </section>
            </div>
          </div>
        ) : null}
      </main>
    </div>
  )
}

export default SupplierDetailPage
