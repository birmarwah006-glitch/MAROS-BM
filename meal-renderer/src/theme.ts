/**
 * MAROS visual identity for Meals.
 *
 * Mirrored from frontend/style.css and reel_planner.py so a Meal, the web app,
 * and the existing interface are recognisably the same product. Do not fork
 * these values.
 */
export const theme = {
  bg: '#0a0a0a',
  surface: '#111111',
  line: 'rgba(255,255,255,0.10)',
  line2: 'rgba(255,255,255,0.18)',
  ink: '#f0ede6',
  muted: '#8a8880',
  green: '#c8f060',
  greenSoft: 'rgba(200,240,96,0.10)',
  blue: '#60c8f0',
  orange: '#f0a060',
  red: '#f06060',
  purple: '#a060f0',

  fontDisplay: 'Syne, Inter, system-ui, sans-serif',
  fontMono: '"DM Mono", ui-monospace, "SF Mono", Menlo, monospace',
} as const

/** 9:16, the only format Meals ship in. */
export const CANVAS = { width: 1080, height: 1920 } as const

/** Everything lives inside this column so nothing crowds the edges. */
export const GUTTER = 88
export const CONTENT_WIDTH = CANVAS.width - GUTTER * 2
