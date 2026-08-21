/**
 * Headless render entry point.
 *
 * Motion Canvas 3.17's FFmpeg exporter runs through the editor UI and there is
 * no headless CLI in that release. Rather than drive the editor with a browser
 * robot — fragile, and it renders whatever the UI happens to be showing — this
 * uses the same public API the editor itself uses: `Renderer` plus a custom
 * `Exporter`.
 *
 * The exporter hands each finished frame back to the Node driver as a PNG,
 * which pipes them into FFmpeg. Deterministic and frame-exact.
 */

import { Renderer, Vector2 } from '@motion-canvas/core'
import type { Exporter, RendererSettings } from '@motion-canvas/core'
// `?project` is the Vite plugin's own convention: it wraps makeProject's
// settings in bootstrap() and returns a fully constructed Project, with the
// logger and meta files the Renderer needs.
import project from './project?project'
import type { Meal, Timing } from './meal'

declare global {
  interface Window {
    __MEAL__: { meal: Meal; timing: Timing }
    renderMeal: (meal: Meal, timing: Timing) => Promise<number>
    /** Exposed from Node by Playwright. */
    emitFrame: (frame: number, dataUrl: string) => Promise<void>
    onRenderProgress?: (frame: number, total: number) => void
  }
}

/** Streams frames out of the page rather than muxing a video in-browser. */
class FrameExporter implements Exporter {
  static readonly id = 'maros-frames'
  static readonly displayName = 'MAROS frames'

  static async create() {
    return new FrameExporter()
  }

  /** Never surfaced — this exporter is only ever selected programmatically. */
  static meta() {
    return null as never
  }

  async handleFrame(canvas: HTMLCanvasElement, frame: number) {
    await window.emitFrame(frame, canvas.toDataURL('image/png'))
  }
}

window.renderMeal = async (meal, timing) => {
  const fps = meal.render?.fps ?? 30
  const width = meal.render?.width ?? 1080
  const height = meal.render?.height ?? 1920

  // The visual runs a touch past the narration so the closing beat can land.
  const totalSeconds = timing.duration + 0.6
  const totalFrames = Math.ceil(totalSeconds * fps)

  // Renderer resolves the exporter by id off the project's meta, so the
  // exporter is registered there rather than through a plugin wrapper.
  const exporters = project.meta.rendering.exporter.exporters
  if (!exporters.some((e: { id: string }) => e.id === FrameExporter.id)) {
    exporters.push(FrameExporter as never)
  }

  const renderer = new Renderer(project)

  renderer.onFrameChanged.subscribe(() => {
    window.onRenderProgress?.(0, totalFrames)
  })

  const settings: RendererSettings = {
    name: meal.id,
    fps,
    range: [0, totalSeconds],
    size: new Vector2(width, height),
    resolutionScale: 1,
    colorSpace: 'srgb',
    background: '#0a0a0a',
    exporter: { name: FrameExporter.id, options: {} },
  }

  await renderer.render(settings)
  return totalFrames
}
