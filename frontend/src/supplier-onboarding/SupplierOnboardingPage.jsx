import { useState } from 'react'
import { BRAND_LOGO_192_URL } from '../lib/brandAssets'
import LegalFooter from '../landing-page/LegalFooter'

const BENEFITS = [
  {
    title: 'Qualified buyer queries',
    body: 'Receive purchase requests from buyers who already know the product, quantity, location, and timeline.',
  },
  {
    title: 'Less back-and-forth',
    body: 'ZWIG collects buyer requirements upfront so your team can quote directly instead of repeating discovery calls.',
  },
  {
    title: 'Category visibility',
    body: 'Show your catalog, cities served, commercial terms, and documents in one supplier profile.',
  },
  {
    title: 'Pilot-first partnership',
    body: 'Start with a lightweight pilot and move to a paid partnership only after ZWIG brings useful buyer demand.',
  },
]

const STEPS = [
  'Add company and contact details',
  'Upload catalog, GST, and LOI documents',
  'ZWIG verifies your supplier profile',
  'Start receiving relevant buyer requirements',
]

const CATEGORY_OPTIONS = [
  'Precious metals',
  'Pharmaceuticals',
  'Electronics',
  'Industrial products',
  'Packaging',
  'Gifting',
  'Other',
]

const DOCUMENT_UPLOADS = [
  { key: 'catalog', label: 'Product catalog / price list' },
  { key: 'gst_certificate', label: 'GST certificate' },
  { key: 'loi', label: 'Signed LOI / pilot agreement' },
  { key: 'company_profile', label: 'Company profile / brochure' },
]

function SupplierHeader({ compact = false }) {
  return (
    <header className="border-b border-slate-200 bg-[#fcfcf9]/90 backdrop-blur">
      <div className="mx-auto flex max-w-6xl items-center justify-between px-4 py-4 sm:px-6">
        <a href="/" className="inline-flex items-center gap-3" aria-label="ZWIG home">
          <img src={BRAND_LOGO_192_URL} alt="ZWIG" className="h-9 w-9 object-contain" />
          <div>
            <p className="text-sm font-semibold uppercase tracking-[0.24em] text-slate-500">ZWIG</p>
            <p className="hidden text-xs text-slate-500 sm:block">Supplier onboarding</p>
          </div>
        </a>
        {compact ? (
          <a className="text-sm font-medium text-slate-600 transition hover:text-slate-950" href="/supplier-onboarding">
            Back
          </a>
        ) : (
          <a
            className="rounded-full bg-slate-950 px-5 py-2.5 text-sm font-medium text-white transition hover:bg-slate-800"
            href="/supplier-onboarding/start"
          >
            Start supplier onboarding
          </a>
        )}
      </div>
    </header>
  )
}

function SupplierOnboardingLanding() {
  return (
    <div className="min-h-screen bg-[#ecece6] text-slate-950">
      <SupplierHeader />

      <main>
        <section className="mx-auto grid max-w-6xl gap-10 px-4 py-14 sm:px-6 sm:py-20 lg:grid-cols-[1.05fr_0.95fr] lg:items-center">
          <div>
            <p className="text-xs font-semibold uppercase tracking-[0.22em] text-brand">For suppliers</p>
            <h1 className="mt-4 max-w-3xl text-[38px] font-semibold leading-[1.05] tracking-[-0.03em] sm:text-5xl lg:text-[64px]">
              Get buyer queries without chasing every lead yourself.
            </h1>
            <p className="mt-6 max-w-2xl text-base leading-8 text-slate-600 sm:text-lg">
              ZWIG helps suppliers receive structured procurement requirements from buyers, compare fit quickly, and
              respond with quotes without spending hours calling and qualifying every enquiry.
            </p>
            <div className="mt-8 flex flex-col gap-3 sm:flex-row">
              <a
                className="inline-flex items-center justify-center rounded-full bg-brand px-6 py-3 text-sm font-semibold text-white transition hover:bg-brand-deep"
                href="/supplier-onboarding/start"
              >
                Start supplier onboarding
              </a>
              <a
                className="inline-flex items-center justify-center rounded-full border border-slate-300 bg-white px-6 py-3 text-sm font-semibold text-slate-800 transition hover:border-slate-400"
                href="#how-it-works"
              >
                See how it works
              </a>
            </div>
          </div>

          <div className="rounded-[2rem] border border-slate-200 bg-[#fcfcf9] p-5 shadow-[0_24px_80px_rgba(15,23,42,0.08)]">
            <div className="rounded-[1.5rem] bg-slate-950 p-5 text-white">
              <div className="flex items-center justify-between gap-4">
                <p className="text-sm font-semibold">New qualified query</p>
                <span className="rounded-full bg-emerald-400/15 px-3 py-1 text-xs font-medium text-emerald-200">
                  Ready to quote
                </span>
              </div>
              <div className="mt-6 rounded-2xl bg-white p-4 text-slate-950">
                <p className="text-xs font-semibold uppercase tracking-[0.18em] text-slate-400">Buyer requirement</p>
                <p className="mt-3 text-lg font-semibold">500g 999 gold bullion, GST bill, Mumbai</p>
                <dl className="mt-4 grid grid-cols-2 gap-3 text-sm">
                  <div className="rounded-xl bg-slate-50 p-3">
                    <dt className="text-slate-500">Quantity</dt>
                    <dd className="mt-1 font-semibold">500 grams</dd>
                  </div>
                  <div className="rounded-xl bg-slate-50 p-3">
                    <dt className="text-slate-500">Timeline</dt>
                    <dd className="mt-1 font-semibold">Today</dd>
                  </div>
                  <div className="rounded-xl bg-slate-50 p-3">
                    <dt className="text-slate-500">Buyer type</dt>
                    <dd className="mt-1 font-semibold">Business</dd>
                  </div>
                  <div className="rounded-xl bg-slate-50 p-3">
                    <dt className="text-slate-500">Need</dt>
                    <dd className="mt-1 font-semibold">Quote only</dd>
                  </div>
                </dl>
              </div>
              <p className="mt-4 text-sm leading-6 text-slate-300">
                Your team receives the important details first, then decides whether to quote.
              </p>
            </div>
          </div>
        </section>

        <section className="border-y border-slate-200 bg-[#fcfcf9] py-14 sm:py-18">
          <div className="mx-auto max-w-6xl px-4 sm:px-6">
            <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
              {BENEFITS.map((benefit) => (
                <article key={benefit.title} className="rounded-2xl border border-slate-200 bg-white p-5">
                  <h2 className="text-base font-semibold text-slate-950">{benefit.title}</h2>
                  <p className="mt-3 text-sm leading-6 text-slate-600">{benefit.body}</p>
                </article>
              ))}
            </div>
          </div>
        </section>

        <section id="how-it-works" className="mx-auto max-w-6xl px-4 py-16 sm:px-6 sm:py-20">
          <div className="grid gap-10 lg:grid-cols-[0.9fr_1.1fr] lg:items-start">
            <div>
              <p className="text-xs font-semibold uppercase tracking-[0.22em] text-brand">How it works</p>
              <h2 className="mt-3 text-3xl font-semibold tracking-[-0.02em] sm:text-4xl">
                A cleaner way to turn ZWIG buyer demand into supplier revenue.
              </h2>
              <p className="mt-4 text-base leading-7 text-slate-600">
                Complete your supplier profile once. ZWIG uses it to understand what you sell, where you serve, and how
                buyers should reach you.
              </p>
            </div>
            <ol className="grid gap-3">
              {STEPS.map((step, index) => (
                <li key={step} className="flex gap-4 rounded-2xl border border-slate-200 bg-white p-5">
                  <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-full bg-brand text-sm font-semibold text-white">
                    {index + 1}
                  </span>
                  <p className="pt-1 text-base font-medium text-slate-900">{step}</p>
                </li>
              ))}
            </ol>
          </div>
        </section>
      </main>

      <LegalFooter />
    </div>
  )
}

function Field({ label, children }) {
  return (
    <label className="block text-sm font-medium text-slate-700">
      {label}
      <div className="mt-2">{children}</div>
    </label>
  )
}

function TextInput(props) {
  return (
    <input
      {...props}
      className="w-full rounded-xl border border-slate-200 bg-white px-4 py-3 text-sm text-slate-900 outline-none transition placeholder:text-slate-400 focus:border-slate-400"
    />
  )
}

function SupplierOnboardingForm() {
  const [submitted, setSubmitted] = useState(false)

  const handleSubmit = (event) => {
    event.preventDefault()
    setSubmitted(true)
    window.scrollTo({ top: 0, behavior: 'smooth' })
  }

  return (
    <div className="min-h-screen bg-[#ecece6] text-slate-950">
      <SupplierHeader compact />

      <main className="mx-auto max-w-5xl px-4 py-10 sm:px-6 sm:py-14">
        {submitted ? (
          <div className="mb-6 rounded-2xl border border-emerald-200 bg-emerald-50 px-5 py-4 text-sm leading-6 text-emerald-900">
            Supplier onboarding details captured in this UI preview. Backend storage can be connected next.
          </div>
        ) : null}

        <div className="rounded-[2rem] border border-slate-200 bg-[#fcfcf9] p-6 shadow-[0_20px_80px_rgba(15,23,42,0.06)] sm:p-8">
          <p className="text-xs font-semibold uppercase tracking-[0.22em] text-brand">Supplier profile</p>
          <h1 className="mt-3 text-3xl font-semibold tracking-[-0.02em] sm:text-4xl">Start supplier onboarding</h1>
          <p className="mt-4 max-w-2xl text-sm leading-7 text-slate-600">
            Add company details, business categories, and documents so ZWIG can verify the supplier profile and route
            qualified buyer queries to the right contact.
          </p>

          <form onSubmit={handleSubmit} className="mt-8 space-y-8">
            <section>
              <h2 className="text-lg font-semibold text-slate-950">Company details</h2>
              <div className="mt-4 grid gap-4 sm:grid-cols-2">
                <Field label="Company name">
                  <TextInput required name="company_name" placeholder="INCREDIBLE SUPPLIERS PRIVATE LIMITED" />
                </Field>
                <Field label="Brand / trade name">
                  <TextInput name="brand_name" placeholder="Supplier brand name" />
                </Field>
                <Field label="Owner name">
                  <TextInput name="owner_name" placeholder="Owner / proprietor name" />
                </Field>
                <Field label="Contact person">
                  <TextInput name="contact_person" placeholder="Person handling ZWIG queries" />
                </Field>
                <Field label="GST number">
                  <TextInput name="gst_number" placeholder="GSTIN" />
                </Field>
                <Field label="Website">
                  <TextInput name="website" type="url" placeholder="https://example.com" />
                </Field>
              </div>
            </section>

            <section>
              <h2 className="text-lg font-semibold text-slate-950">Contact details</h2>
              <div className="mt-4 grid gap-4 sm:grid-cols-2">
                <Field label="Phone number">
                  <TextInput required name="phone" placeholder="+91..." />
                </Field>
                <Field label="WhatsApp number">
                  <TextInput name="whatsapp" placeholder="+91..." />
                </Field>
                <Field label="Email">
                  <TextInput name="email" type="email" placeholder="sales@example.com" />
                </Field>
                <Field label="City">
                  <TextInput required name="city" placeholder="Rajkot" />
                </Field>
                <Field label="State">
                  <TextInput name="state" placeholder="Gujarat" />
                </Field>
                <Field label="Pincode">
                  <TextInput name="pincode" placeholder="360001" />
                </Field>
              </div>
              <Field label="Registered / business address">
                <textarea
                  name="address"
                  className="min-h-28 w-full rounded-xl border border-slate-200 bg-white px-4 py-3 text-sm text-slate-900 outline-none transition placeholder:text-slate-400 focus:border-slate-400"
                  placeholder="Full business address"
                />
              </Field>
            </section>

            <section>
              <h2 className="text-lg font-semibold text-slate-950">Business categories</h2>
              <div className="mt-4 grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
                {CATEGORY_OPTIONS.map((category) => (
                  <label key={category} className="flex items-center gap-3 rounded-xl border border-slate-200 bg-white px-4 py-3 text-sm text-slate-700">
                    <input className="h-4 w-4 rounded border-slate-300 text-brand" name="categories" type="checkbox" value={category} />
                    {category}
                  </label>
                ))}
              </div>
              <Field label="Catalog notes / products supplied">
                <textarea
                  name="catalog_notes"
                  className="min-h-28 w-full rounded-xl border border-slate-200 bg-white px-4 py-3 text-sm text-slate-900 outline-none transition placeholder:text-slate-400 focus:border-slate-400"
                  placeholder="Example: 999 gold bullion, silver bars, electronics accessories, pharma products..."
                />
              </Field>
            </section>

            <section>
              <h2 className="text-lg font-semibold text-slate-950">Upload documents</h2>
              <div className="mt-4 grid gap-4 sm:grid-cols-2">
                {DOCUMENT_UPLOADS.map((doc) => (
                  <label key={doc.key} className="rounded-2xl border border-dashed border-slate-300 bg-white p-5">
                    <span className="text-sm font-semibold text-slate-900">{doc.label}</span>
                    <span className="mt-1 block text-xs leading-5 text-slate-500">PDF, image, Excel, or document file</span>
                    <input className="mt-4 block w-full text-sm text-slate-600 file:mr-4 file:rounded-full file:border-0 file:bg-slate-950 file:px-4 file:py-2 file:text-sm file:font-medium file:text-white" name={doc.key} type="file" />
                  </label>
                ))}
              </div>
            </section>

            <section>
              <h2 className="text-lg font-semibold text-slate-950">Pilot and commercial notes</h2>
              <textarea
                name="commercial_notes"
                className="mt-4 min-h-28 w-full rounded-xl border border-slate-200 bg-white px-4 py-3 text-sm text-slate-900 outline-none transition placeholder:text-slate-400 focus:border-slate-400"
                placeholder="Add any pilot terms, preferred payment model, cities served, MOQ, delivery timelines, or follow-up notes."
              />
            </section>

            <div className="flex flex-col gap-3 border-t border-slate-200 pt-6 sm:flex-row sm:items-center sm:justify-between">
              <p className="text-xs leading-5 text-slate-500">
                This page is a UI preview. Submission storage and secure uploads can be connected in the next step.
              </p>
              <button
                type="submit"
                className="inline-flex items-center justify-center rounded-full bg-brand px-6 py-3 text-sm font-semibold text-white transition hover:bg-brand-deep"
              >
                Submit onboarding details
              </button>
            </div>
          </form>
        </div>
      </main>

      <LegalFooter />
    </div>
  )
}

function SupplierOnboardingPage({ mode = 'landing' }) {
  if (mode === 'form') {
    return <SupplierOnboardingForm />
  }
  return <SupplierOnboardingLanding />
}

export default SupplierOnboardingPage
