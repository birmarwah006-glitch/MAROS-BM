/**
 * MAROS visual primitives for Meals (spec section 34).
 *
 * Deliberately the first fifteen only: hook text, captions, code editor,
 * typing, highlighting, terminal, arrow, box, pointer, variable, loop,
 * input/output, takeaway, practice. No characters, whiteboards or 3D — those
 * come after the pipeline is proven.
 *
 * Every primitive is a plain builder returning a Motion Canvas node. They
 * carry no knowledge of any specific Meal.
 */

import { Rect, Txt, Line, Layout, type Node } from '@motion-canvas/2d'
import { createRef, type Reference } from '@motion-canvas/core'
import { theme, CONTENT_WIDTH } from '../theme'

/** Card chrome shared by every boxed element, so Meals look like one product. */
export function panel(children: Node[], opts: { padding?: number; fill?: string } = {}) {
  return (
    <Rect
      layout
      direction="column"
      radius={20}
      fill={opts.fill ?? theme.surface}
      stroke={theme.line}
      lineWidth={2}
      padding={opts.padding ?? 40}
      gap={24}
      width={CONTENT_WIDTH}
    >
      {children}
    </Rect>
  )
}

/** Small mono label. Used for filenames, section marks, terminal chrome. */
export function eyebrow(text: string, color: string = theme.muted) {
  return (
    <Txt
      text={text.toUpperCase()}
      fontFamily={theme.fontMono}
      fontSize={26}
      letterSpacing={3}
      fill={color}
    />
  )
}

/**
 * Big statement text. Used for the hook, the question and the takeaway.
 *
 * KNOWN LIMITATION — inline per-word colouring is not available here.
 * Motion Canvas 3.17 does not lay out sibling Txt nodes: several Txt children
 * of one container all paint at the container origin and pile up on each
 * other, whether or not `layout` is set on the parent, the children, or a Rect
 * wrapper around each word. All three were tried and rendered identically
 * broken.
 *
 * A single Txt with `textWrap` wraps correctly — it is what the caption track
 * uses — so a statement is one Txt in one colour. Emphasis is carried by the
 * beat's eyebrow instead (for example a green "TAKEAWAY" above the line),
 * which reads as deliberate rather than as a missing feature.
 *
 * The `emphasis` field stays in the schema: it is renderer-agnostic content,
 * and a future renderer can honour it without a schema change.
 */
export function statement(
  text: string,
  _emphasis: string[] = [],
  color: string = theme.ink,
  size = 82,
) {
  return (
    <Txt
      text={text}
      textWrap
      textAlign="center"
      width={CONTENT_WIDTH}
      fontFamily={theme.fontDisplay}
      fontWeight={800}
      fontSize={size}
      lineHeight={size * 1.14}
      fill={color}
    />
  )
}

/** A labelled box. `kind` maps onto the flow node kinds in the schema. */
export function box(label: string, kind: string, ref?: Reference<Rect>) {
  const palette: Record<string, { fill: string; stroke: string; text: string }> = {
    box: { fill: theme.surface, stroke: theme.line2, text: theme.ink },
    actor: { fill: 'rgba(96,200,240,0.10)', stroke: 'rgba(96,200,240,0.45)', text: theme.blue },
    value: { fill: theme.greenSoft, stroke: 'rgba(200,240,96,0.45)', text: theme.green },
    decision: { fill: 'rgba(240,160,96,0.10)', stroke: 'rgba(240,160,96,0.45)', text: theme.orange },
    output: { fill: theme.greenSoft, stroke: 'rgba(200,240,96,0.45)', text: theme.green },
  }
  const c = palette[kind] ?? palette.box
  const mono = kind === 'value' || kind === 'output'

  return (
    <Rect
      ref={ref}
      layout
      radius={14}
      fill={c.fill}
      stroke={c.stroke}
      lineWidth={2}
      padding={[22, 36]}
      alignItems="center"
      justifyContent="center"
      opacity={0}
    >
      <Txt
        text={label}
        fontFamily={mono ? theme.fontMono : theme.fontDisplay}
        fontWeight={mono ? 400 : 600}
        fontSize={mono ? 38 : 40}
        fill={c.text}
      />
    </Rect>
  )
}

/** A downward connector with an arrowhead, optionally labelled. */
export function arrow(length = 64, label?: string, ref?: Reference<Layout>) {
  return (
    <Layout ref={ref} direction="row" alignItems="center" gap={14} opacity={0}>
      <Line
        points={[[0, 0], [0, length]]}
        stroke={theme.muted}
        lineWidth={3}
        endArrow
        arrowSize={12}
      />
      {label ? (
        <Txt text={label} fontFamily={theme.fontMono} fontSize={26} fill={theme.muted} />
      ) : null}
    </Layout>
  )
}

/** A variable as a named memory cell — `x` above a boxed value. */
export function variableCell(name: string, value: string, valueRef?: Reference<Txt>) {
  return (
    <Layout layout direction="column" alignItems="center" gap={10}>
      <Rect layout>
        <Txt text={name} fontFamily={theme.fontMono} fontSize={38} fill={theme.blue} />
      </Rect>
      <Line points={[[0, 0], [0, 28]]} stroke={theme.muted} lineWidth={3} endArrow arrowSize={10} />
      <Rect
        radius={12}
        fill={theme.surface}
        stroke={theme.line2}
        lineWidth={3}
        minWidth={150}
        padding={[22, 32]}
        layout
        justifyContent="center"
      >
        <Txt ref={valueRef} text={value} fontFamily={theme.fontMono} fontSize={44} fill={theme.ink} />
      </Rect>
    </Layout>
  )
}

/** A sequence of cells with a pointer that can travel along them. */
export function sequenceRow(
  items: string[],
  cellRefs: Reference<Rect>[],
) {
  return (
    <Layout layout direction="row" gap={16} alignItems="center">
      {items.map((item, i) => (
        <Rect
          ref={cellRefs[i]}
          radius={12}
          fill={theme.surface}
          stroke={theme.line2}
          lineWidth={3}
          minWidth={140}
          padding={[24, 26]}
          layout
          justifyContent="center"
        >
          <Txt text={item} fontFamily={theme.fontMono} fontSize={40} fill={theme.ink} />
        </Rect>
      ))}
    </Layout>
  )
}

export { createRef }
