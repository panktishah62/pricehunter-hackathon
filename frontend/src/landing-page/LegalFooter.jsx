import { BRAND_LOGO_192_URL } from '../lib/brandAssets'

const COMPANY_ADDRESS_LINES = [
  'B-21, Suraj Appartment-2,',
  'Shroff Road,',
  'Rajkot,',
  'Gujarat - 360001,',
  'India',
]

const COMPANY_LINKS = [
  { label: 'Features', href: '/#features' },
  { label: 'About Us', href: '/about' },
  { label: 'Contact Us', href: '/contact' },
]

const LEGAL_LINKS = [
  { label: 'Support', href: '/support' },
  { label: 'Terms', href: '/terms' },
  { label: 'Privacy', href: '/privacy-policy' },
  { label: 'Security', href: '/security' },
]

function LegalFooter() {
  return (
    <footer className="border-t border-[#e5e7eb] bg-[#fbfaf8]">
      <div className="mx-auto max-w-[1180px] px-4 py-14 sm:px-6 sm:py-16">
        <div className="grid gap-10 lg:grid-cols-[1.4fr_0.8fr_0.8fr] lg:gap-12">
          <div>
            <a href="/" className="inline-flex items-center gap-3" aria-label="Zwig home">
              <img src={BRAND_LOGO_192_URL} alt="" className="h-10 w-10 rounded-xl" />
              <span className="text-xl font-black tracking-[-0.04em] text-[#111827]">zwig</span>
            </a>
            <p className="mt-5 max-w-sm text-sm leading-7 text-[#64748b]">
              AI-native procurement for India — find suppliers, compare quotes, negotiate prices, and close purchases with
              verified proof.
            </p>
            <p className="mt-6 text-sm text-[#64748b]">
              <span className="font-semibold text-[#111827]">Connect</span>
              <br />
              <a className="mt-1 inline-block font-medium transition hover:text-[#111827]" href="mailto:hello@zwig.in">
                hello@zwig.in
              </a>
            </p>
          </div>

          <nav aria-label="Company">
            <p className="text-xs font-bold uppercase tracking-[0.2em] text-[#111827]">Company</p>
            <ul className="mt-5 flex flex-col gap-3.5 text-sm text-[#64748b]">
              {COMPANY_LINKS.map((link) => (
                <li key={link.href}>
                  <a className="font-medium transition hover:text-[#111827]" href={link.href}>
                    {link.label}
                  </a>
                </li>
              ))}
            </ul>
          </nav>

          <nav aria-label="Legal">
            <p className="text-xs font-bold uppercase tracking-[0.2em] text-[#111827]">Legal</p>
            <ul className="mt-5 flex flex-col gap-3.5 text-sm text-[#64748b]">
              {LEGAL_LINKS.map((link) => (
                <li key={link.label}>
                  <a className="font-medium transition hover:text-[#111827]" href={link.href}>
                    {link.label}
                  </a>
                </li>
              ))}
            </ul>
          </nav>
        </div>

        <div className="mt-12 flex flex-col gap-3 border-t border-[#e5e7eb] pt-8 text-sm text-[#64748b] sm:flex-row sm:items-center sm:justify-between">
          <span>© 2026 ZWIG. All rights reserved.</span>
          <span className="text-xs sm:text-sm">INCREDIBLE LIFESTYLE SOLUTION PRIVATE LIMITED</span>
        </div>
      </div>
    </footer>
  )
}

export { COMPANY_ADDRESS_LINES }
export default LegalFooter
