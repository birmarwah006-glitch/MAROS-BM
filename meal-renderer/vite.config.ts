import { defineConfig } from 'vite'
import motionCanvasPkg from '@motion-canvas/vite-plugin'

// @motion-canvas/vite-plugin 3.17 is CJS with no `exports` map, so under ESM
// the callable lands at `.default.default`. Unwrapped once here rather than at
// every call site.
const motionCanvas = (
  (motionCanvasPkg as unknown as { default?: unknown }).default ?? motionCanvasPkg
) as typeof motionCanvasPkg

// Rendering is driven headlessly by render.mjs, not by the editor UI. The
// plugin is here only to resolve `?scene` imports and serve the project.
export default defineConfig({
  plugins: [motionCanvas({ project: ['./src/project.ts'] })],
  server: { port: 9000 },
})
