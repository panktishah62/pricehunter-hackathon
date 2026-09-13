import { useState } from 'react'
import { BRAND_LOGO_192_URL } from '../lib/brandAssets'
import LegalFooter, { COMPANY_ADDRESS_LINES } from './LegalFooter'

function MarketingNav() {
  return (
    <nav className="sticky top-0 z-50 border-b border-black/5 bg-white/82 backdrop-blur-xl">
      <div className="mx-auto flex max-w-[1180px] items-center justify-between gap-4 px-4 py-3.5 sm:px-6">
        <a href="/" className="flex items-center gap-3" aria-label="Zwig home">
          <img src={BRAND_LOGO_192_URL} alt="Zwig" className="h-9 w-9 rounded-xl" />
          <span className="text-lg font-black tracking-[-0.03em] text-[#111827]">ZWIG</span>
        </a>
        <a
          href="#/app"
          className="inline-flex items-center justify-center rounded-full bg-[#f97316] px-5 py-2.5 text-sm font-semibold text-white shadow-[0_14px_28px_-14px_rgba(249,115,22,0.7)] transition hover:bg-[#ea580c]"
        >
          Search Products
        </a>
      </div>
    </nav>
  )
}

function LegalSection({ title, children }) {
  return (
    <section className="border-t border-[#e5e7eb] pt-8 first:border-t-0 first:pt-0">
      <h2 className="text-xl font-black tracking-[-0.03em] text-[#111827]">{title}</h2>
      <div className="mt-4 space-y-4 text-base leading-8 text-[#4b5563]">{children}</div>
    </section>
  )
}

function LegalDocumentLayout({ title, eyebrow = 'Legal', intro, lastUpdated, showBackLink = false, children }) {
  return (
    <div className="min-h-screen overflow-x-hidden bg-[#fbfaf8] font-display text-[#111827]">
      <MarketingNav />

      <main>
        <section className="relative overflow-hidden px-4 pb-10 pt-14 sm:px-6 sm:pb-12 sm:pt-20">
          <div className="absolute left-1/2 top-0 h-[520px] w-[980px] -translate-x-1/2 rounded-full bg-[radial-gradient(circle,rgba(249,115,22,0.14),transparent_68%)]" />
          <div className="absolute inset-x-0 top-0 h-[480px] bg-[linear-gradient(180deg,#fff7ed_0%,rgba(255,247,237,0)_72%)]" />

          <div className="relative mx-auto max-w-[1180px]">
            {showBackLink ? (
              <a
                href="/"
                className="inline-flex items-center gap-2 text-sm font-semibold text-[#64748b] transition hover:text-[#111827]"
              >
                <span aria-hidden="true">←</span>
                Back to home
              </a>
            ) : null}
            <div className="max-w-3xl">
              <p className={`text-xs font-bold uppercase tracking-[0.22em] text-[#f97316] ${showBackLink ? 'mt-8' : ''}`}>
                {eyebrow}
              </p>
              <h1 className="mt-4 text-[40px] font-black leading-[0.98] tracking-[-0.06em] text-[#0f172a] sm:text-5xl lg:text-[56px]">
                {title}
              </h1>
              {intro ? <p className="mt-5 max-w-2xl text-lg leading-8 text-[#4b5563]">{intro}</p> : null}
              {lastUpdated ? (
                <p className="mt-5 text-sm font-semibold uppercase tracking-[0.14em] text-[#94a3b8]">
                  Last updated: {lastUpdated}
                </p>
              ) : null}
            </div>
          </div>
        </section>

        <section className="border-t border-[#e5e7eb] bg-white px-4 py-12 sm:px-6 sm:py-16">
          <div className="mx-auto max-w-[1180px]">
            <article className="mx-auto max-w-4xl rounded-[28px] border border-[#e5e7eb] bg-[#fbfaf8] p-6 sm:p-8 lg:p-10">
              {children}
            </article>
          </div>
        </section>
      </main>

      <LegalFooter />
    </div>
  )
}

function LegalEmailLink({ children = 'hello@zwig.in' }) {
  return (
    <a className="font-semibold text-[#111827] underline decoration-[#fdba74] underline-offset-4 transition hover:text-[#f97316]" href="mailto:hello@zwig.in">
      {children}
    </a>
  )
}

function AddressBlock() {
  return (
    <address className="mt-2 not-italic text-base leading-7 text-[#334155]">
      {COMPANY_ADDRESS_LINES.map((line) => (
        <span key={line} className="block">
          {line}
        </span>
      ))}
    </address>
  )
}

function AboutPage() {
  const pillars = [
    {
      title: 'Supplier discovery',
      body: 'Built for business procurement teams that need clearer information before they commit.',
    },
    {
      title: 'Price comparison',
      body: 'Built for business procurement teams that need clearer information before they commit.',
    },
    {
      title: 'Sourcing workflows',
      body: 'Built for business procurement teams that need clearer information before they commit.',
    },
  ]

  return (
    <div className="min-h-screen overflow-x-hidden bg-[#fbfaf8] font-display text-[#111827]">
      <MarketingNav />

      <main>
        <section className="relative overflow-hidden px-4 pb-16 pt-14 sm:px-6 sm:pb-20 sm:pt-20">
          <div className="absolute left-1/2 top-0 h-[520px] w-[980px] -translate-x-1/2 rounded-full bg-[radial-gradient(circle,rgba(249,115,22,0.14),transparent_68%)]" />
          <div className="absolute inset-x-0 top-0 h-[480px] bg-[linear-gradient(180deg,#fff7ed_0%,rgba(255,247,237,0)_72%)]" />

          <div className="relative mx-auto max-w-[1180px]">
            <div className="max-w-3xl">
              <p className="text-xs font-bold uppercase tracking-[0.22em] text-[#f97316]">About us</p>
              <h1 className="mt-4 text-[40px] font-black leading-[0.98] tracking-[-0.06em] text-[#0f172a] sm:text-5xl lg:text-[64px]">
                About ZWIG
              </h1>

              <div className="mt-8 space-y-5 text-lg leading-8 text-[#4b5563]">
                <p>
                  ZWIG is an AI-native procurement and supplier discovery platform that helps businesses discover
                  suppliers, compare pricing, negotiate commercial outcomes, and streamline sourcing workflows.
                </p>
                <p>ZWIG is operated by INCREDIBLE LIFESTYLE SOLUTION PRIVATE LIMITED.</p>
                <p>
                  The platform connects buyers and suppliers across categories including precious metals,
                  pharmaceuticals, electronics, industrial products, and other business procurement segments.
                </p>
              </div>
            </div>
          </div>
        </section>

        <section className="border-t border-[#e5e7eb] bg-white px-4 py-16 sm:px-6 sm:py-20">
          <div className="mx-auto max-w-[1180px]">
            <div className="grid gap-4 md:grid-cols-3">
              {pillars.map((item, index) => (
                <article
                  key={item.title}
                  className="rounded-[28px] border border-[#e5e7eb] bg-[#fbfaf8] p-6 shadow-sm transition hover:-translate-y-1 hover:shadow-lg"
                >
                  <span className="inline-flex h-10 w-10 items-center justify-center rounded-2xl bg-[#fff7ed] text-sm font-black text-[#f97316]">
                    {String(index + 1).padStart(2, '0')}
                  </span>
                  <h3 className="mt-5 text-xl font-black tracking-[-0.03em] text-[#111827]">{item.title}</h3>
                  <p className="mt-3 text-sm leading-7 text-[#64748b]">{item.body}</p>
                </article>
              ))}
            </div>
          </div>
        </section>
      </main>

      <LegalFooter />
    </div>
  )
}

function ContactPage() {
  const [submitted, setSubmitted] = useState(false)

  const handleSubmit = (event) => {
    event.preventDefault()
    setSubmitted(true)
  }

  const contactDetails = [
    {
      label: 'Company Name',
      value: 'INCREDIBLE LIFESTYLE SOLUTION PRIVATE LIMITED',
    },
    {
      label: 'Brand',
      value: 'ZWIG',
    },
    {
      label: 'Email',
      value: (
        <a className="font-semibold text-[#111827] transition hover:text-[#f97316]" href="mailto:hello@zwig.in">
          hello@zwig.in
        </a>
      ),
    },
    {
      label: 'Website',
      value: (
        <a className="font-semibold text-[#111827] transition hover:text-[#f97316]" href="https://www.zwig.in">
          https://www.zwig.in
        </a>
      ),
    },
  ]

  return (
    <div className="min-h-screen overflow-x-hidden bg-[#fbfaf8] font-display text-[#111827]">
      <MarketingNav />

      <main>
        <section className="relative overflow-hidden px-4 pb-10 pt-14 sm:px-6 sm:pb-12 sm:pt-20">
          <div className="absolute left-1/2 top-0 h-[520px] w-[980px] -translate-x-1/2 rounded-full bg-[radial-gradient(circle,rgba(249,115,22,0.14),transparent_68%)]" />
          <div className="absolute inset-x-0 top-0 h-[480px] bg-[linear-gradient(180deg,#fff7ed_0%,rgba(255,247,237,0)_72%)]" />

          <div className="relative mx-auto max-w-[1180px]">
            <div className="max-w-3xl">
              <p className="text-xs font-bold uppercase tracking-[0.22em] text-[#f97316]">Company contact</p>
              <h1 className="mt-4 text-[40px] font-black leading-[0.98] tracking-[-0.06em] text-[#0f172a] sm:text-5xl lg:text-[56px]">
                Contact Us
              </h1>
            </div>
          </div>
        </section>

        <section className="border-t border-[#e5e7eb] bg-white px-4 py-12 sm:px-6 sm:py-16">
          <div className="mx-auto grid max-w-[1180px] gap-8 lg:grid-cols-[0.95fr_1.05fr] lg:items-start">
            <div className="rounded-[28px] border border-[#e5e7eb] bg-[#fbfaf8] p-6 sm:p-8">
              <h2 className="text-xl font-black tracking-[-0.03em] text-[#111827]">Get in touch</h2>

              <dl className="mt-8 space-y-5">
                {contactDetails.map((item) => (
                  <div key={item.label} className="rounded-[22px] border border-[#e5e7eb] bg-white px-5 py-4">
                    <dt className="text-xs font-bold uppercase tracking-[0.16em] text-[#94a3b8]">{item.label}</dt>
                    <dd className="mt-2 text-base leading-7 text-[#334155]">{item.value}</dd>
                  </div>
                ))}
              </dl>
            </div>

            <form
              onSubmit={handleSubmit}
              className="rounded-[28px] border border-[#e5e7eb] bg-white p-6 shadow-[0_28px_90px_-44px_rgba(15,23,42,0.35)] sm:p-8"
            >
              <h2 className="text-xl font-black tracking-[-0.03em] text-[#111827]">Send a message</h2>
              <p className="mt-3 text-sm leading-7 text-[#64748b]">
                Share your query and our team will respond at hello@zwig.in.
              </p>

              <div className="mt-8 space-y-5">
                <label className="block text-sm font-bold text-[#334155]">
                  Name
                  <input
                    required
                    className="mt-2 w-full rounded-2xl border border-[#e5e7eb] bg-[#fbfaf8] px-4 py-3.5 text-sm font-medium text-[#111827] outline-none transition focus:border-[#f97316] focus:ring-4 focus:ring-orange-100"
                    name="name"
                    type="text"
                  />
                </label>
                <label className="block text-sm font-bold text-[#334155]">
                  Email
                  <input
                    required
                    className="mt-2 w-full rounded-2xl border border-[#e5e7eb] bg-[#fbfaf8] px-4 py-3.5 text-sm font-medium text-[#111827] outline-none transition focus:border-[#f97316] focus:ring-4 focus:ring-orange-100"
                    name="email"
                    type="email"
                  />
                </label>
                <label className="block text-sm font-bold text-[#334155]">
                  Message
                  <textarea
                    required
                    className="mt-2 min-h-36 w-full resize-y rounded-2xl border border-[#e5e7eb] bg-[#fbfaf8] px-4 py-3.5 text-sm font-medium text-[#111827] outline-none transition focus:border-[#f97316] focus:ring-4 focus:ring-orange-100"
                    name="message"
                  />
                </label>
              </div>

              <button
                type="submit"
                className="mt-6 inline-flex w-full items-center justify-center rounded-full bg-[#f97316] px-5 py-3.5 text-sm font-semibold text-white shadow-[0_14px_28px_-14px_rgba(249,115,22,0.7)] transition hover:bg-[#ea580c]"
              >
                Submit
              </button>

              {submitted ? (
                <p className="mt-4 rounded-2xl border border-emerald-200 bg-emerald-50 px-4 py-3 text-sm text-emerald-800">
                  Thank you. Please email hello@zwig.in if your request is urgent.
                </p>
              ) : null}
            </form>
          </div>
        </section>
      </main>

      <LegalFooter />
    </div>
  )
}

function PrivacyPolicyPage() {
  return (
    <LegalDocumentLayout title="Privacy Policy" lastUpdated="June 20, 2026">
      <div className="space-y-8">
        <p className="text-base leading-8 text-[#4b5563]">
          ZWIG is operated by INCREDIBLE LIFESTYLE SOLUTION PRIVATE LIMITED.
        </p>

        <LegalSection title="Information collected">
          <p>
            We may collect information that you provide while using the website or platform, including your name, email
            address, business details, search requests, product requirements, location preferences, uploaded product
            images, chat messages, and procurement-related instructions.
          </p>
          <p>
            We may also collect technical information such as device identifiers, browser information, IP address,
            session activity, usage analytics, error logs, and cookies required to operate and improve the service.
          </p>
        </LegalSection>

        <LegalSection title="Usage of information">
          <p>
            Information is used to operate ZWIG, process procurement requests, discover suppliers, compare pricing,
            contact vendors where applicable, improve platform reliability, provide customer support, prevent misuse,
            and maintain business records.
          </p>
        </LegalSection>

        <LegalSection title="Cookies">
          <p>
            We may use cookies and similar technologies to remember preferences, maintain sessions, measure website
            performance, support analytics, and improve user experience. Users may manage cookies through their browser
            settings, although disabling cookies may affect platform functionality.
          </p>
        </LegalSection>

        <LegalSection title="Data retention">
          <p>
            We retain information for as long as reasonably necessary to provide the service, comply with legal
            obligations, resolve disputes, enforce agreements, maintain security, and improve the platform.
          </p>
        </LegalSection>

        <LegalSection title="User rights">
          <p>
            Subject to applicable law, users may request access, correction, update, or deletion of their personal
            information. Requests may be sent to hello@zwig.in and will be reviewed in accordance with applicable legal
            requirements.
          </p>
        </LegalSection>

        <LegalSection title="Contact information">
          <p>
            For privacy-related questions, contact INCREDIBLE LIFESTYLE SOLUTION PRIVATE LIMITED at{' '}
            <LegalEmailLink />.
          </p>
          <div className="rounded-[22px] border border-[#e5e7eb] bg-white px-5 py-4">
            <p className="text-xs font-bold uppercase tracking-[0.16em] text-[#94a3b8]">Registered Office</p>
            <AddressBlock />
          </div>
        </LegalSection>
      </div>
    </LegalDocumentLayout>
  )
}

function TermsPage() {
  return (
    <LegalDocumentLayout title="Terms of Service" lastUpdated="June 20, 2026">
      <div className="space-y-8">
        <p className="text-base leading-8 text-[#4b5563]">
          ZWIG is operated by INCREDIBLE LIFESTYLE SOLUTION PRIVATE LIMITED.
        </p>

        <LegalSection title="Platform usage">
          <p>
            ZWIG provides AI-native procurement, supplier discovery, pricing comparison, and sourcing workflow tools.
            Users may use the platform only for lawful business purposes and in accordance with these terms.
          </p>
        </LegalSection>

        <LegalSection title="User obligations">
          <p>
            Users are responsible for providing accurate procurement requirements, complying with applicable laws,
            verifying supplier suitability before purchase, and ensuring that any transaction entered into with a
            supplier meets their commercial and regulatory requirements.
          </p>
        </LegalSection>

        <LegalSection title="Intellectual property">
          <p>
            The ZWIG name, platform design, software, workflows, content, and related intellectual property are
            protected by applicable intellectual property laws. Users may not copy, modify, reverse engineer, or misuse
            the platform except as expressly permitted.
          </p>
        </LegalSection>

        <LegalSection title="Disclaimer">
          <p>
            Supplier prices, availability, delivery timelines, and commercial terms may change. ZWIG provides supplier
            discovery and pricing assistance, but users should independently verify all details before making a purchase
            or entering into a transaction.
          </p>
        </LegalSection>

        <LegalSection title="Limitation of liability">
          <p>
            To the maximum extent permitted by applicable law, INCREDIBLE LIFESTYLE SOLUTION PRIVATE LIMITED will not be
            liable for indirect, incidental, consequential, special, punitive, or business interruption losses arising
            from use of the website or platform.
          </p>
        </LegalSection>

        <LegalSection title="Contact details">
          <p>
            For questions about these terms, contact <LegalEmailLink />.
          </p>
          <div className="rounded-[22px] border border-[#e5e7eb] bg-white px-5 py-4">
            <p className="text-xs font-bold uppercase tracking-[0.16em] text-[#94a3b8]">Registered Office</p>
            <AddressBlock />
          </div>
        </LegalSection>
      </div>
    </LegalDocumentLayout>
  )
}

function SecurityPage() {
  return (
    <LegalDocumentLayout
      eyebrow="Trust & safety"
      title="Security"
      intro="Zwig is built to handle real procurement requests with care. We take reasonable measures to protect your data and the integrity of verified quotes and supplier interactions."
      showBackLink
    >
      <div className="space-y-10">
        <LegalSection title="Data protection">
          <p>
            Data in transit is encrypted using industry-standard TLS. Access to production systems is restricted to
            authorized personnel and monitored for unusual activity.
          </p>
          <p>
            Account credentials, procurement requests, and supplier communications are stored on secure infrastructure.
            We do not sell your personal or business data to third parties.
          </p>
        </LegalSection>

        <LegalSection title="Verification records">
          <p>
            Call recordings, message transcripts, and proof artifacts from supplier outreach are stored securely and
            linked only to the procurement requests that generated them.
          </p>
          <p>
            These records help you audit quotes, compare offers, and confirm what was agreed before you place an order.
            Access is limited to your organization and Zwig systems required to deliver the service.
          </p>
        </LegalSection>

        <LegalSection title="Reporting issues">
          <p>
            If you discover a security concern or suspect unauthorized access to your account, contact us promptly. We
            review reports and take appropriate action.
          </p>
          <p>
            Email <LegalEmailLink /> with a description of the issue and any relevant details. For urgent matters,
            include &ldquo;Security&rdquo; in the subject line.
          </p>
        </LegalSection>
      </div>
    </LegalDocumentLayout>
  )
}

function SupportPage() {
  const commonTopics = [
    {
      title: 'Account & data deletion',
      description: 'Remove your account and personal data',
      href: 'mailto:hello@zwig.in?subject=Account%20Deletion%20Request',
    },
    {
      title: 'Privacy & data rights',
      description: 'Access, correction, or deletion requests',
      href: '/privacy-policy',
    },
    {
      title: 'Getting started',
      description: 'How Zwig works and your first procurement request',
      href: '/#features',
    },
    {
      title: 'Report a bug',
      description: 'Something not working as expected',
      href: 'mailto:hello@zwig.in?subject=Bug%20Report',
    },
  ]

  const relatedResources = [
    {
      title: 'Privacy Policy',
      description: 'How we collect and use your data',
      href: '/privacy-policy',
    },
    {
      title: 'Terms of Service',
      description: 'Rules for using Zwig',
      href: '/terms',
    },
    {
      title: 'Security',
      description: 'How we protect your information',
      href: '/security',
    },
  ]

  const handleSubmit = (event) => {
    event.preventDefault()
    const formData = new FormData(event.currentTarget)
    const name = String(formData.get('name') || '').trim()
    const email = String(formData.get('email') || '').trim()
    const message = String(formData.get('message') || '').trim()
    const subject = encodeURIComponent(`Zwig support request from ${name || 'user'}`)
    const body = encodeURIComponent(`Name: ${name}\nEmail: ${email}\n\n${message}`)
    window.location.href = `mailto:hello@zwig.in?subject=${subject}&body=${body}`
  }

  return (
    <div className="min-h-screen overflow-x-hidden bg-[#fbfaf8] font-display text-[#111827]">
      <MarketingNav />

      <main>
        <section className="relative overflow-hidden px-4 pb-10 pt-14 sm:px-6 sm:pb-12 sm:pt-20">
          <div className="absolute left-1/2 top-0 h-[520px] w-[980px] -translate-x-1/2 rounded-full bg-[radial-gradient(circle,rgba(249,115,22,0.14),transparent_68%)]" />
          <div className="absolute inset-x-0 top-0 h-[480px] bg-[linear-gradient(180deg,#fff7ed_0%,rgba(255,247,237,0)_72%)]" />

          <div className="relative mx-auto max-w-[1180px]">
            <a
              href="/"
              className="inline-flex items-center gap-2 text-sm font-semibold text-[#64748b] transition hover:text-[#111827]"
            >
              <span aria-hidden="true">←</span>
              Back to home
            </a>

            <div className="mt-8 max-w-3xl">
              <p className="text-xs font-bold uppercase tracking-[0.22em] text-[#f97316]">Support</p>
              <h1 className="mt-4 text-[40px] font-black leading-[0.98] tracking-[-0.06em] text-[#0f172a] sm:text-5xl lg:text-[56px]">
                How can we help?
              </h1>
              <p className="mt-5 max-w-2xl text-lg leading-8 text-[#4b5563]">
                Account access, procurement requests, quote results, platform issues, privacy requests, and more — our
                team is here to help.
              </p>
              <p className="mt-4 max-w-2xl text-base leading-8 text-[#64748b]">
                Email us directly and we&apos;ll get back to you as soon as we can — typically within 1–2 business days.
              </p>

              <a
                href="mailto:hello@zwig.in"
                className="mt-8 inline-flex items-center rounded-[22px] border border-[#fed7aa] bg-white px-6 py-4 text-lg font-black tracking-[-0.02em] text-[#111827] shadow-sm transition hover:border-[#f97316] hover:text-[#f97316]"
              >
                hello@zwig.in
              </a>
            </div>
          </div>
        </section>

        <section className="border-t border-[#e5e7eb] bg-white px-4 py-12 sm:px-6 sm:py-16">
          <div className="mx-auto grid max-w-[1180px] gap-8 lg:grid-cols-2 lg:items-start">
            <div className="rounded-[28px] border border-[#e5e7eb] bg-[#fbfaf8] p-6 sm:p-8">
              <h2 className="text-xl font-black tracking-[-0.03em] text-[#111827]">Common topics</h2>
              <ul className="mt-6 space-y-3">
                {commonTopics.map((topic) => (
                  <li key={topic.title}>
                    <a
                      href={topic.href}
                      className="group flex items-start justify-between gap-4 rounded-[22px] border border-[#e5e7eb] bg-white px-5 py-4 transition hover:border-[#fdba74] hover:shadow-sm"
                    >
                      <span>
                        <span className="block text-sm font-bold text-[#111827]">{topic.title}</span>
                        <span className="mt-1 block text-sm leading-6 text-[#64748b]">{topic.description}</span>
                      </span>
                      <span
                        aria-hidden="true"
                        className="mt-0.5 shrink-0 text-lg font-semibold text-[#94a3b8] transition group-hover:text-[#f97316]"
                      >
                        →
                      </span>
                    </a>
                  </li>
                ))}
              </ul>
            </div>

            <div className="rounded-[28px] border border-[#e5e7eb] bg-[#fbfaf8] p-6 sm:p-8">
              <h2 className="text-xl font-black tracking-[-0.03em] text-[#111827]">What to include</h2>
              <p className="mt-4 text-base leading-8 text-[#4b5563]">
                To help us resolve your issue faster, please share:
              </p>
              <ul className="mt-5 space-y-3 text-sm leading-7 text-[#4b5563]">
                <li className="flex gap-3">
                  <span className="mt-2 h-1.5 w-1.5 shrink-0 rounded-full bg-[#f97316]" />
                  The email or phone number on your Zwig account
                </li>
                <li className="flex gap-3">
                  <span className="mt-2 h-1.5 w-1.5 shrink-0 rounded-full bg-[#f97316]" />
                  Your browser, device type, and operating system version
                </li>
                <li className="flex gap-3">
                  <span className="mt-2 h-1.5 w-1.5 shrink-0 rounded-full bg-[#f97316]" />
                  A brief description of what happened and what you expected
                </li>
                <li className="flex gap-3">
                  <span className="mt-2 h-1.5 w-1.5 shrink-0 rounded-full bg-[#f97316]" />
                  Screenshots or screen recordings, if available
                </li>
              </ul>
            </div>
          </div>
        </section>

        <section className="border-t border-[#e5e7eb] bg-[#fbfaf8] px-4 py-12 sm:px-6 sm:py-16">
          <div className="mx-auto max-w-[1180px]">
            <div className="mx-auto max-w-3xl rounded-[28px] border border-[#e5e7eb] bg-white p-6 shadow-[0_28px_90px_-44px_rgba(15,23,42,0.35)] sm:p-8">
              <h2 className="text-xl font-black tracking-[-0.03em] text-[#111827]">Contact support</h2>
              <p className="mt-3 text-sm leading-7 text-[#64748b]">
                Prefer to send a message from here? Fill out the form and your email app will open with your request
                ready to send.
              </p>

              <form onSubmit={handleSubmit} className="mt-8 space-y-5">
                <label className="block text-sm font-bold text-[#334155]">
                  Name
                  <input
                    required
                    className="mt-2 w-full rounded-2xl border border-[#e5e7eb] bg-[#fbfaf8] px-4 py-3.5 text-sm font-medium text-[#111827] outline-none transition focus:border-[#f97316] focus:ring-4 focus:ring-orange-100"
                    name="name"
                    type="text"
                  />
                </label>
                <label className="block text-sm font-bold text-[#334155]">
                  Email
                  <input
                    required
                    className="mt-2 w-full rounded-2xl border border-[#e5e7eb] bg-[#fbfaf8] px-4 py-3.5 text-sm font-medium text-[#111827] outline-none transition focus:border-[#f97316] focus:ring-4 focus:ring-orange-100"
                    name="email"
                    type="email"
                  />
                </label>
                <label className="block text-sm font-bold text-[#334155]">
                  Message
                  <textarea
                    required
                    className="mt-2 min-h-36 w-full resize-y rounded-2xl border border-[#e5e7eb] bg-[#fbfaf8] px-4 py-3.5 text-sm font-medium text-[#111827] outline-none transition focus:border-[#f97316] focus:ring-4 focus:ring-orange-100"
                    name="message"
                  />
                </label>

                <button
                  type="submit"
                  className="inline-flex w-full items-center justify-center rounded-full bg-[#f97316] px-5 py-3.5 text-sm font-semibold text-white shadow-[0_14px_28px_-14px_rgba(249,115,22,0.7)] transition hover:bg-[#ea580c] sm:w-auto"
                >
                  Send message
                </button>
              </form>
            </div>
          </div>
        </section>

        <section className="border-t border-[#e5e7eb] bg-white px-4 py-12 sm:px-6 sm:py-16">
          <div className="mx-auto max-w-[1180px]">
            <h2 className="text-xl font-black tracking-[-0.03em] text-[#111827]">Related resources</h2>
            <ul className="mt-6 grid gap-3 sm:grid-cols-2">
              {relatedResources.map((resource) => (
                <li key={resource.title}>
                  <a
                    href={resource.href}
                    className="group flex h-full items-start justify-between gap-4 rounded-[22px] border border-[#e5e7eb] bg-[#fbfaf8] px-5 py-4 transition hover:border-[#fdba74] hover:bg-white hover:shadow-sm"
                  >
                    <span>
                      <span className="block text-sm font-bold text-[#111827]">{resource.title}</span>
                      <span className="mt-1 block text-sm leading-6 text-[#64748b]">{resource.description}</span>
                    </span>
                    <span
                      aria-hidden="true"
                      className="mt-0.5 shrink-0 text-lg font-semibold text-[#94a3b8] transition group-hover:text-[#f97316]"
                    >
                      →
                    </span>
                  </a>
                </li>
              ))}
            </ul>
          </div>
        </section>
      </main>

      <LegalFooter />
    </div>
  )
}

export { AboutPage, ContactPage, PrivacyPolicyPage, SecurityPage, SupportPage, TermsPage }
