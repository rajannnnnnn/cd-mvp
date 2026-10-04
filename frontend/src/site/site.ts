import '@/styles/fonts'
import '@/styles/app.css'
import './site.css'
import { HI } from './hi'
import { ladderLevels, stepFor } from './ladder'

type Lang = 'en' | 'hi'
const LANG_KEY = 'saathi.lang'
const $ = <T extends HTMLElement>(s: string, r: ParentNode = document) => r.querySelector<T>(s)
const $$ = <T extends HTMLElement>(s: string, r: ParentNode = document) => Array.from(r.querySelectorAll<T>(s))
const safe = <T>(f: () => T, d: T): T => { try { return f() } catch { return d } }

/* ------------------------------------------------------------------ language */
const brand = document.querySelector<HTMLMetaElement>('meta[name="brand"]')?.content ?? 'Saathi'
function applyLang(lang: Lang) {
  document.documentElement.lang = lang
  for (const el of $$('[data-i18n],[data-i18n-html]')) {
    const key = el.dataset.i18n ?? el.dataset.i18nHtml!
    if (el.dataset.en === undefined) el.dataset.en = el.innerHTML
    el.innerHTML = lang === 'hi' && HI[key] ? HI[key].replaceAll('%BRAND%', brand) : el.dataset.en
  }
  const lbl = $('#lang-label'); if (lbl) lbl.textContent = lang === 'hi' ? 'EN' : 'हिं'
  safe(() => localStorage.setItem(LANG_KEY, lang), undefined)
  document.dispatchEvent(new CustomEvent('langchange', { detail: lang }))
}
const initialLang = (): Lang => safe(() => (localStorage.getItem(LANG_KEY) as Lang) || (navigator.language.startsWith('hi') ? 'hi' : 'en'), 'en')
let lang: Lang = initialLang()
$('#lang-toggle')?.addEventListener('click', () => { lang = lang === 'en' ? 'hi' : 'en'; applyLang(lang) })
if (lang === 'hi') applyLang('hi')

/* ------------------------------------------------------------------ theme + misc */
$('#theme-toggle')?.addEventListener('click', () => {
  const next = document.documentElement.dataset.theme === 'dark' ? 'light' : 'dark'
  document.documentElement.dataset.theme = next; safe(() => localStorage.setItem('saathi.theme', next), undefined)
})
const yr = $('#year'); if (yr) yr.textContent = String(new Date().getFullYear())

/* ------------------------------------------------------------------ scroll reveal */
const reduced = matchMedia('(prefers-reduced-motion: reduce)').matches
if ('IntersectionObserver' in window && !reduced) {
  const io = new IntersectionObserver((es) => es.forEach((e) => { if (e.isIntersecting) { e.target.classList.add('in'); io.unobserve(e.target) } }), { threshold: 0.12, rootMargin: '0px 0px -40px 0px' })
  $$('.reveal').forEach((el, i) => { el.style.transitionDelay = `${(i % 4) * 70}ms`; io.observe(el) })
} else $$('.reveal').forEach((el) => el.classList.add('in'))

/* ------------------------------------------------------------------ hero conversation loop */
function heroChat() {
  const box = $('#hero-chat'); if (!box) return
  const msgs = $$<HTMLElement>('.chat-msg', box)
  const typing = $('#hero-typing')!
  const alert = $('#hero-alert')
  const status = $('#hero-status')
  const wait = (ms: number) => new Promise<void>((r) => setTimeout(r, reduced ? Math.min(ms, 50) : ms))
  const toBottom = () => { box.scrollTop = box.scrollHeight }
  let alive = true
  document.addEventListener('visibilitychange', () => { alive = !document.hidden })
  const run = async () => {
    for (;;) {
      msgs.forEach((m) => m.classList.remove('show')); alert?.classList.remove('!opacity-100', '!translate-y-0')
      if (status) status.textContent = 'business account'
      await wait(900)
      for (const m of msgs) {
        while (!alive) await wait(500)
        if (m.dataset.who === 'a') {
          if (status) status.textContent = 'typing…'
          typing.classList.add('show'); box.appendChild(typing); toBottom()
          await wait(1100 + Math.min(1400, (m.textContent ?? '').length * 18))
          typing.classList.remove('show'); if (status) status.textContent = 'online'
        } else await wait(900)
        m.classList.add('show'); box.insertBefore(m, typing); toBottom()
        await wait(450)
      }
      await wait(500); alert?.classList.add('!opacity-100', '!translate-y-0')
      await wait(5200)
      if (reduced) return
    }
  }
  void run()
}
heroChat()

/* ------------------------------------------------------------------ price ladder demo (mirrors the server's concession schedule) */
function ladderDemo() {
  const root = $('#ladder-demo'); if (!root) return
  const list = $<HTMLInputElement>('#ld-list')!, floor = $<HTMLInputElement>('#ld-floor')!, steps = $<HTMLInputElement>('#ld-steps')!, ask = $<HTMLInputElement>('#ld-ask')!
  const out = $('#ld-path')!, note = $('#ld-note')!, reply = $('#ld-reply')!
  const inr = (n: number) => `₹${Math.round(n).toLocaleString('en-IN')}`
  const render = () => {
    const t = (en: string, hi: string) => (lang === 'hi' ? hi : en)
    let l = +list.value, f = +floor.value
    if (f > l) { f = l; floor.value = String(f) }
    floor.max = String(l)
    const n = +steps.value, a = +ask.value
    const lv = ladderLevels(l, f, n, 10)
    $('#ld-list-v')!.textContent = inr(l); $('#ld-floor-v')!.textContent = inr(f); $('#ld-steps-v')!.textContent = String(n); $('#ld-ask-v')!.textContent = inr(a)
    const pills = [`<span class="step-pill rounded-full bg-surface px-3.5 py-1.5 text-[14px] font-bold tnum shadow-card">${inr(l)}</span>`]
    lv.forEach((p, i) => pills.push(`<span class="text-muted">→</span><span class="step-pill rounded-full ${i === lv.length - 1 ? 'bg-brand text-white' : 'bg-surface'} px-3.5 py-1.5 text-[14px] font-bold tnum shadow-card">${inr(p)}</span>`))
    out.innerHTML = pills.join('')
    note.innerHTML = lv.length
      ? `<span>🔒</span><span>${t(`Your lowest price (${inr(f)}) is never shown to the AI or the customer. After ${inr(lv.at(-1)!)} the assistant says “final” and brings you in if they want less.`, `आपकी सबसे कम कीमत (${inr(f)}) AI या ग्राहक को कभी नहीं दिखती। ${inr(lv.at(-1)!)} के बाद सहायक “फ़ाइनल” कहता है और ग्राहक और कम माँगे तो आपको बुलाता है।`)}</span>`
      : `<span>ℹ️</span><span>${t('Fixed price: the assistant quotes your price and does not bargain.', 'तय कीमत: सहायक आपकी कीमत बताता है और मोलभाव नहीं करता।')}</span>`
    // the assistant's reply to what the customer asked for: the lowest step still at or above the ask, never below the floor
    let msg: string
    if (a >= l) msg = t(`Great, ${inr(l)} it is. Shall I confirm your order?`, `बढ़िया, ${inr(l)} में पक्का। ऑर्डर कन्फ़र्म करूँ?`)
    else if (!lv.length) msg = t(`The price is ${inr(l)}, I’m afraid that’s fixed.`, `कीमत ${inr(l)} है जी, यह तय है।`)
    else {
      const ok = stepFor(lv, a)!
      const isFinal = ok === lv.at(-1)
      msg = ok === a ? t(`${inr(a)} works for me. Shall I confirm your order?`, `${inr(a)} में हो जाएगा। ऑर्डर कन्फ़र्म करूँ?`) : a < f
        ? t(`${inr(ok)} is the lowest I can do ji. If you need less, let me check with the owner.`, `${inr(ok)} सबसे कम हो पाएगा जी। इससे कम चाहिए तो मैं मालिक से पूछ लेता हूँ।`)
        : t(`I can do ${inr(ok)} for you${isFinal ? ', that’s my final price' : ''}. Shall I confirm?`, `आपके लिए ${inr(ok)} कर देता हूँ${isFinal ? ', यह मेरा फ़ाइनल है' : ''}। कन्फ़र्म करूँ?`)
    }
    reply.textContent = msg
  }
  ;[list, floor, steps, ask].forEach((el) => el.addEventListener('input', render))
  document.addEventListener('langchange', render)
  render()
}
ladderDemo()
