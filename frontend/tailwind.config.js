const c = (v) => `rgb(var(--${v}) / <alpha-value>)`
/** @type {import('tailwindcss').Config} */
export default {
  content: ['./app/index.html', './src/**/*.{ts,tsx}'],
  darkMode: ['class', '[data-theme="dark"]'],
  theme: {
    extend: {
      colors: {
        bg: c('bg'), surface: c('surface'), surface2: c('surface2'), ink: c('ink'), muted: c('muted'), line: c('line'),
        brand: c('brand'), 'brand-ink': c('brand-ink'), 'brand-soft': c('brand-soft'), accent: c('accent'), 'accent-soft': c('accent-soft'),
        danger: c('danger'), 'danger-soft': c('danger-soft'), warn: c('warn'), info: c('info'), 'info-soft': c('info-soft'),
      },
      fontFamily: { sans: ['Inter', 'system-ui', '-apple-system', 'Segoe UI', 'Noto Sans Devanagari', 'sans-serif'], display: ['"Plus Jakarta Sans"', 'Inter', 'system-ui', 'Noto Sans Devanagari', 'sans-serif'] },
      borderRadius: { xl2: '18px' },
      boxShadow: { card: '0 1px 2px rgb(15 31 26 / .04), 0 6px 20px -8px rgb(15 31 26 / .10)', pop: '0 10px 40px -10px rgb(15 31 26 / .35)' },
      keyframes: { fadeUp: { from: { opacity: 0, transform: 'translateY(8px)' }, to: { opacity: 1, transform: 'none' } }, pulseDot: { '0%,100%': { opacity: 1 }, '50%': { opacity: .35 } } },
      animation: { fadeUp: 'fadeUp .35s ease both', pulseDot: 'pulseDot 1.4s ease-in-out infinite' },
    },
  },
  plugins: [],
}
