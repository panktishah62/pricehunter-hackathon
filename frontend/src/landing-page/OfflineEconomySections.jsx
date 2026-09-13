import { useEffect, useRef, useState } from 'react'

const TRENDING_SEARCHES = [
  'Paracetamol 650 mg bulk rates',
  '999 gold live rate Mumbai',
  'Samsung S24 bulk procurement',
  'Industrial MRO supplier quotes',
  'Laryngoscope set with GST invoice',
  '995 bullion 500 grams Ahmedabad',
  'Packaging pouches wholesale pricing',
  'Medical gloves bulk order',
  '22K gold supplier Rajkot',
  'Automation parts same-day quote',
  'Crocin alternative distributor',
  'Silver 999 live rate today',
  'Corrugated boxes bulk Mumbai',
  'Hospital bed supplier quotes',
  'API distributor Chandigarh',
  'Standing desk under ₹25,000',
  'Surgical instruments GST invoice',
  'Warehouse shelving bulk quotes',
  'Refinery gold bar pricing',
  'Pharma stockist same-day delivery',
]

function Reveal({ children, className = '', delay = 0 }) {
  const ref = useRef(null)
  const [visible, setVisible] = useState(
    () => typeof window !== 'undefined' && window.matchMedia('(prefers-reduced-motion: reduce)').matches,
  )

  useEffect(() => {
    if (visible || !ref.current) {
      return undefined
    }
    const observer = new IntersectionObserver(
      (entries) => {
        if (entries[0].isIntersecting) {
          setVisible(true)
          observer.disconnect()
        }
      },
      { threshold: 0.12 },
    )
    observer.observe(ref.current)
    return () => observer.disconnect()
  }, [visible])

  return (
    <div
      ref={ref}
      className={`transition-all duration-700 ease-out ${
        visible ? 'translate-y-0 opacity-100' : 'translate-y-6 opacity-0'
      } ${className}`}
      style={delay ? { transitionDelay: `${delay}ms` } : undefined}
    >
      {children}
    </div>
  )
}

function SectionHeading({ eyebrow, title, body, align = 'left' }) {
  return (
    <div className={align === 'center' ? 'mx-auto max-w-3xl text-center' : 'max-w-3xl'}>
      {eyebrow ? (
        <p className="text-xs font-bold uppercase tracking-[0.22em] text-[#f97316]">{eyebrow}</p>
      ) : null}
      <h2 className="mt-3 text-[30px] font-black leading-[1.06] tracking-[-0.04em] text-[#111827] sm:text-4xl md:text-[48px]">
        {title}
      </h2>
      {body ? <p className="mt-4 text-base leading-7 text-[#5b6472] sm:text-lg">{body}</p> : null}
    </div>
  )
}

function MarqueeRow({ items, reverse = false }) {
  const doubled = [...items, ...items]
  return (
    <div className="overflow-hidden [mask-image:linear-gradient(to_right,transparent,black_8%,black_92%,transparent)]">
      <div
        className={`marquee-track flex w-max gap-3 ${reverse ? '[animation-direction:reverse]' : ''}`}
        style={{ animationDuration: reverse ? '38s' : '32s' }}
      >
        {doubled.map((item, index) => (
          <span
            key={`${item}-${index}`}
            className="shrink-0 rounded-full border border-[#e5e7eb] bg-white px-5 py-2.5 text-sm font-bold text-[#475569] shadow-sm"
          >
            {item}
          </span>
        ))}
      </div>
    </div>
  )
}

function TrendingSearchesSection() {
  return (
    <section className="overflow-hidden border-t border-[#e5e7eb] bg-white px-4 py-20 sm:px-6 sm:py-28">
      <div className="mx-auto max-w-[1180px]">
        <Reveal>
          <SectionHeading
            align="center"
            eyebrow="Trending on Zwig"
            title="What buyers are searching for"
          />
        </Reveal>
        <Reveal delay={120} className="mt-10 space-y-4">
          <MarqueeRow items={TRENDING_SEARCHES.slice(0, 10)} />
          <MarqueeRow items={TRENDING_SEARCHES.slice(10, 20)} reverse />
        </Reveal>
      </div>
    </section>
  )
}

export { TrendingSearchesSection }
