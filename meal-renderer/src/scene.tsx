/**
 * The Meal scene builder.
 *
 * This is the whole point of the architecture: ONE renderer, MANY Meal JSON
 * files. Nothing here knows about input(), for loops, or any particular Meal.
 * It reads a Meal document plus its timing sidecar and animates whatever it
 * finds.
 *
 * Timing is never authored. Every beat starts when its `narration_anchor` is
 * actually spoken, according to the forced alignment in the timing sidecar. So
 * the visuals cannot drift out of sync with the voice.
 */

import { makeScene2D, Rect, Txt, Layout, Code, Node } from '@motion-canvas/2d'
import {
  createRef, all, waitFor, easeOutCubic, easeInOutCubic, type Reference,
} from '@motion-canvas/core'
import { theme, CANVAS, CONTENT_WIDTH, GUTTER } from './theme'
import { statement, box, variableCell, sequenceRow, eyebrow } from './components/primitives'
import { codePane, terminalPane } from './components/codePane'
import type { ThreadGenerator } from '@motion-canvas/core'
import type {
  Meal, Timing, Scene, CodeEditorVisual, TerminalVisual,
  FlowVisual, TextVisual, VariableVisual, SequenceVisual, PracticeVisual,
} from './meal'

/** Set by main.ts before the project is created. */
declare global {
  interface Window {
    __MEAL__: { meal: Meal; timing: Timing }
  }
}

const FADE = 0.35

/** Absolute-time schedule for the beats, derived from the alignment. */
function schedule(meal: Meal, timing: Timing) {
  const starts = meal.scenes.map((s) =>
    s.narration_anchor !== undefined ? (timing.anchors[s.narration_anchor] ?? 0) : 0,
  )
  return meal.scenes.map((scene, i) => {
    const start = starts[i]
    const nextStart = i + 1 < starts.length ? starts[i + 1] : timing.duration
    const end = Math.max(nextStart, start + (scene.min_duration ?? 0))
    return { scene, start, end }
  })
}

export default makeScene2D(function* (view) {
  const { meal, timing } = window.__MEAL__

  view.fill(theme.bg)

  // ── Persistent chrome ────────────────────────────────────────────────
  const stage = createRef<Layout>()
  const captionRef = createRef<Txt>()

  view.add(
    <>
      {/* Concept label, always present so a viewer joining mid-scroll has context */}
      <Layout layout y={-CANVAS.height / 2 + 120} direction="column" alignItems="center" gap={12}>
        <Txt
          text={meal.title.toUpperCase()}
          fontFamily={theme.fontMono}
          fontSize={26}
          letterSpacing={4}
          fill={theme.muted}
        />
      </Layout>

      {/* Where each beat's visual is mounted */}
      <Layout
        ref={stage}
        layout
        width={CONTENT_WIDTH}
        height={1180}
        direction="column"
        alignItems="center"
        justifyContent="center"
        y={-40}
      />

      {/* Captions. Audio-first product, so these are not optional chrome. */}
      <Layout layout y={CANVAS.height / 2 - 560} width={CANVAS.width - GUTTER} justifyContent="center">
        <Txt
          ref={captionRef}
          text=""
          fontFamily={theme.fontDisplay}
          fontWeight={600}
          fontSize={52}
          lineHeight={70}
          textAlign="center"
          textWrap
          width={CANVAS.width - GUTTER * 2}
          fill={theme.ink}
        />
      </Layout>
    </>,
  )

  // ── Caption track, driven off the same alignment as the beats ────────
  // Runs concurrently with the beat animation for the whole Meal.
  const captionTrack = function* () {
    if (meal.captions?.enabled === false) return
    let cursor = 0
    for (const line of timing.captions) {
      if (line.start > cursor) yield* waitFor(line.start - cursor)
      captionRef().text(line.text)
      cursor = Math.max(line.start, cursor)
      const hold = Math.max(line.end - cursor, 0.08)
      yield* waitFor(hold)
      cursor += hold
    }
    captionRef().text('')
  }

  const beatTrack = function* () {
    let cursor = 0
    for (const { scene, start, end } of schedule(meal, timing)) {
      if (start > cursor) {
        yield* waitFor(start - cursor)
        cursor = start
      }
      const budget = Math.max(end - cursor, 0.4)
      yield* renderBeat(stage, scene, budget, timing)
      cursor += budget
    }
    // Hold the frame to the end of the narration.
    if (timing.duration > cursor) yield* waitFor(timing.duration - cursor)
  }

  yield* all(beatTrack(), captionTrack())
})

/** Swap the stage contents and animate the beat inside its time budget. */
function* renderBeat(
  stage: Reference<Layout>,
  scene: Scene,
  budget: number,
  timing: Timing,
) {
  const container = createRef<Layout>()
  const node = (
    <Layout
      ref={container}
      layout
      direction="column"
      alignItems="center"
      justifyContent="center"
      gap={28}
      opacity={0}
      y={26}
    />
  ) as Layout

  // Clear the previous beat, mount this one.
  stage().removeChildren()
  stage().add(node)

  const build = BUILDERS[scene.visual.type]
  const inner: Built = build
    ? build(scene, timing)
    : { nodes: [] }
  for (const child of inner.nodes) container().add(child)

  yield* all(
    container().opacity(1, FADE, easeOutCubic),
    container().y(0, FADE, easeOutCubic),
  )

  // The beat's own choreography gets whatever time is left.
  const remaining = Math.max(budget - FADE, 0.1)
  if (inner.animate) {
    yield* inner.animate(remaining)
  } else {
    yield* waitFor(remaining)
  }
}

interface Built {
  nodes: Node[]
  animate?: (seconds: number) => ThreadGenerator
}

type Builder = (scene: Scene, timing: Timing) => Built

/**
 * One builder per controlled visual type. The planner may only choose from
 * this set — it never emits animation code — which is what keeps rendering
 * predictable and the LLM's job bounded.
 */
const BUILDERS: Record<string, Builder> = {
  text(scene) {
    const v = scene.visual as TextVisual
    const color = v.tone === 'takeaway' ? theme.ink : theme.ink
    const size = v.tone === 'hook' ? 88 : v.tone === 'takeaway' ? 72 : 76
    const nodes: Node[] = []
    if (v.tone === 'takeaway') nodes.push(eyebrow('takeaway', theme.green) as Node)
    nodes.push(statement(v.text, v.emphasis ?? [], color, size) as Node)
    return { nodes }
  },

  practice(scene) {
    const v = scene.visual as PracticeVisual
    return {
      nodes: [
        eyebrow('your turn', theme.green) as Node,
        statement(v.prompt, [], theme.ink, 62) as Node,
      ],
    }
  },

  flow(scene, timing) {
    const v = scene.visual as FlowVisual
    const nodeRefs = new Map<string, Reference<Rect>>()
    const rows: Node[] = []

    v.nodes.forEach((n, i) => {
      const ref = createRef<Rect>()
      nodeRefs.set(n.id, ref)
      if (i > 0) {
        rows.push(
          <Layout layout opacity={0} direction="column" alignItems="center">
            <Rect width={3} height={44} fill={theme.line2} />
          </Layout> as Node,
        )
      }
      rows.push(box(n.label, n.kind ?? 'box', ref) as Node)
    })

    return {
      nodes: rows,
      *animate(seconds: number) {
        // Reveal each step in turn, spread across the beat's own budget so a
        // flow diagram builds up rather than appearing all at once.
        const reveals = rows.length
        const per = Math.max(seconds / Math.max(reveals, 1), 0.12)
        for (const row of rows) {
          yield* (row as Layout).opacity(1, Math.min(per * 0.55, 0.3), easeOutCubic)
          const rest = per - Math.min(per * 0.55, 0.3)
          if (rest > 0) yield* waitFor(rest)
        }
      },
    }
  },

  variable(scene) {
    const v = scene.visual as VariableVisual
    const valueRefs = v.variables.map(() => createRef<Txt>())
    const cells = v.variables.map((varDef, i) =>
      variableCell(varDef.name, varDef.value ?? '', valueRefs[i]) as Node,
    )
    return {
      nodes: [(<Layout direction="row" gap={64}>{cells}</Layout>) as Node],
      *animate(seconds: number) {
        const steps = v.steps ?? []
        if (!steps.length) {
          yield* waitFor(seconds)
          return
        }
        const per = seconds / steps.length
        for (const step of steps) {
          const idx = v.variables.findIndex((x) => x.name === step.name)
          if (idx >= 0) {
            // The rebinding itself is the teaching moment, so it is animated.
            yield* valueRefs[idx]().scale(1.14, per * 0.18, easeOutCubic)
            valueRefs[idx]().text(step.value)
            yield* valueRefs[idx]().scale(1, per * 0.18, easeInOutCubic)
          }
          yield* waitFor(Math.max(per * 0.64, 0.05))
        }
      },
    }
  },

  sequence(scene) {
    const v = scene.visual as SequenceVisual
    const cellRefs = v.items.map(() => createRef<Rect>())
    const pointerRef = createRef<Txt>()
    const emitRef = createRef<Txt>()

    return {
      nodes: [
        (v.label ? eyebrow(v.label) : <Layout layout />) as Node,
        sequenceRow(v.items, cellRefs) as Node,
        (
          <Rect layout>
            <Txt
              ref={pointerRef}
              text={v.pointer_label ? `${v.pointer_label} = ` : ''}
              fontFamily={theme.fontMono}
              fontSize={40}
              fill={theme.green}
            />
          </Rect>
        ) as Node,
        (
          <Rect layout>
            <Txt ref={emitRef} text="" fontFamily={theme.fontMono} fontSize={38} fill={theme.muted} />
          </Rect>
        ) as Node,
      ],
      *animate(seconds: number) {
        const steps: { index: number; emit?: string }[] =
          v.steps ?? v.items.map((_, i) => ({ index: i }))
        const per = seconds / Math.max(steps.length, 1)
        for (const step of steps) {
          // Highlight the current cell; the pointer is the loop variable.
          yield* all(
            ...cellRefs.map((r, i) =>
              all(
                r().stroke(i === step.index ? theme.green : theme.line2, per * 0.2),
                r().fill(i === step.index ? theme.greenSoft : theme.surface, per * 0.2),
              ),
            ),
          )
          if (v.pointer_label) {
            pointerRef().text(`${v.pointer_label} = ${v.items[step.index]}`)
          }
          if (step.emit) emitRef().text(step.emit)
          yield* waitFor(Math.max(per * 0.8, 0.05))
        }
      },
    }
  },

  code_editor(scene) {
    const v = scene.visual as CodeEditorVisual
    const codeRef = createRef<Code>()
    return {
      nodes: [codePane(v.filename ?? 'main.py', codeRef, '') as Node],
      *animate(seconds: number) {
        const actions = v.actions ?? []
        if (!actions.length) {
          codeRef().code(v.code)
          yield* waitFor(seconds)
          return
        }

        // Typing actions get time proportional to how much they type, so a
        // long line does not race and a short one does not crawl.
        const typing = actions.filter((a) => a.action === 'type')
        const typedChars = typing.reduce((n, a) => n + (a.text?.length ?? 0), 0)
        const otherCount = actions.length - typing.length
        const otherBudget = Math.min(otherCount * 0.6, seconds * 0.4)
        const typeBudget = Math.max(seconds - otherBudget, 0.3)

        let built = ''
        for (const action of actions) {
          if (action.action === 'type' && action.text) {
            const share = typedChars ? action.text.length / typedChars : 1
            const duration = Math.max(typeBudget * share, 0.25)
            built = built ? `${built}\n${action.text}` : action.text
            yield* codeRef().code(built, duration)
          } else if (action.action === 'highlight' && action.text) {
            const range = codeRef().findFirstRange(action.text)
            if (range) {
              yield* codeRef().selection(range, 0.3)
              yield* waitFor(Math.max(otherBudget / Math.max(otherCount, 1) - 0.3, 0.1))
            }
          } else if (action.action === 'clear') {
            yield* codeRef().code('', 0.2)
          } else {
            yield* waitFor(Math.max(otherBudget / Math.max(otherCount, 1), 0.1))
          }
        }
        codeRef().selection(codeRef().findAllRanges(''))
      },
    }
  },

  terminal(scene) {
    const v = scene.visual as TerminalVisual
    const bodyRef = createRef<Txt>()
    const ex = v.execution

    // Guard rail, enforced at render time as well as in the validator: an
    // unverified execution must never be shown as if the code had run.
    if (!ex.verified || ex.source === 'unverified') {
      throw new Error(
        `Meal has an unverified terminal execution. Run meals/verify.py. ` +
        `Never claim code executed if it did not.`,
      )
    }

    return {
      nodes: [terminalPane(v.command ?? 'python main.py', bodyRef) as Node],
      *animate(seconds: number) {
        // A real TTY echoes typed input; a captured pipe does not. So stdin is
        // interleaved here rather than being faked into the recorded stdout.
        const stdout = ex.stdout
        const stdin = v.stdin ?? []
        let composed = stdout
        if (stdin.length) {
          // Insert the echo right after the first prompt, where a person
          // would actually have typed it.
          const firstNewline = stdout.indexOf('\n')
          const promptEnd = firstNewline === -1 ? stdout.length : firstNewline
          composed = stdout.slice(0, promptEnd) + stdin[0] + '\n' + stdout.slice(promptEnd).replace(/^\n/, '')
        }

        // Reveal progressively, as output actually appears.
        const chars = composed.length
        const reveal = Math.min(seconds * 0.6, 1.6)
        const steps = Math.max(Math.min(chars, 40), 1)
        for (let i = 1; i <= steps; i++) {
          bodyRef().text(composed.slice(0, Math.ceil((chars * i) / steps)))
          yield* waitFor(reveal / steps)
        }
        yield* waitFor(Math.max(seconds - reveal, 0.1))
      },
    }
  },
}
