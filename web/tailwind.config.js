/** @type {import('tailwindcss').Config} */

// Tokens are NOT invented here. Every value below maps 1:1 onto a CSS variable
// declared in src/styles/index.css, which in turn mirrors the existing
// frontend/style.css. Keeping one source of truth means the new app, the
// existing /app frontend, and reel_planner.py's rendered video all agree on
// what "MAROS green" is.
const token = (name) => `var(--${name})`

export default {
  content: ['./index.html', './src/**/*.{ts,tsx}'],
  theme: {
    extend: {
      colors: {
        bg: token('bg'),
        surface: token('surface'),
        surface2: token('surface2'),
        line: token('border'),
        line2: token('border2'),
        ink: token('text'),
        muted: token('muted'),
        muted2: token('muted2'),
        green: token('green'),
        'green-soft': token('green-soft'),
        'green-line': token('green-line'),
        'accent-ink': token('accent-ink'),
        blue: token('blue'),
        'blue-soft': token('blue-soft'),
        'blue-line': token('blue-line'),
        orange: token('orange'),
        red: token('red'),
        'red-soft': token('red-soft'),
        'red-line': token('red-line'),
        purple: token('purple'),
      },
      fontFamily: {
        display: ['Syne', 'ui-sans-serif', 'system-ui', 'sans-serif'],
        mono: ['DM Mono', 'ui-monospace', 'SFMono-Regular', 'monospace'],
      },
      borderRadius: {
        DEFAULT: 'var(--radius)',
        lg: 'var(--radius-lg)',
      },
      // A deliberately tight type scale. The brief rules out "giant text
      // everywhere", so hierarchy comes from weight and spacing, not size.
      fontSize: {
        micro: ['0.6875rem', { lineHeight: '1rem', letterSpacing: '0.08em' }],
        xs: ['0.75rem', { lineHeight: '1.1rem' }],
        sm: ['0.8125rem', { lineHeight: '1.25rem' }],
        base: ['0.9375rem', { lineHeight: '1.6rem' }],
        lg: ['1.0625rem', { lineHeight: '1.7rem' }],
        xl: ['1.375rem', { lineHeight: '1.8rem', letterSpacing: '-0.01em' }],
        '2xl': ['1.75rem', { lineHeight: '2.1rem', letterSpacing: '-0.02em' }],
        '3xl': ['2.25rem', { lineHeight: '2.5rem', letterSpacing: '-0.025em' }],
        '4xl': ['3rem', { lineHeight: '3.15rem', letterSpacing: '-0.03em' }],
      },
      keyframes: {
        'fade-up': {
          from: { opacity: '0', transform: 'translateY(6px)' },
          to: { opacity: '1', transform: 'translateY(0)' },
        },
        shimmer: {
          '100%': { transform: 'translateX(100%)' },
        },
      },
      animation: {
        'fade-up': 'fade-up 0.28s cubic-bezier(0.2, 0.7, 0.3, 1) both',
      },
    },
  },
  plugins: [],
}
