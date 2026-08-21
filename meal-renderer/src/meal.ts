/**
 * Types for the Meal JSON contract, mirroring meals/schema/meal.schema.json.
 *
 * The renderer is data-driven: it knows these SHAPES, never any particular
 * Meal. Adding a Meal means adding a JSON file, not touching this code.
 */

export type Beat =
  | 'hook' | 'question' | 'concept' | 'visual'
  | 'code' | 'execution' | 'takeaway' | 'practice'

export interface TextVisual {
  type: 'text'
  text: string
  emphasis?: string[]
  tone?: 'hook' | 'question' | 'takeaway'
}

export interface CodeAction {
  action: 'type' | 'highlight' | 'cursor' | 'select' | 'error' | 'clear'
  narration_anchor?: string
  lines?: number[]
  text?: string
  speed_cps?: number
}

export interface CodeEditorVisual {
  type: 'code_editor'
  language: 'python'
  filename?: string
  code: string
  show_line_numbers?: boolean
  actions?: CodeAction[]
}

export interface Execution {
  verified: boolean
  source: 'glot.io' | 'local_sandbox' | 'unverified'
  executed_at?: string
  stdout: string
  stderr?: string
  exit_code: number
}

export interface TerminalVisual {
  type: 'terminal'
  command?: string
  stdin?: string[]
  execution: Execution
}

export interface FlowNode {
  id: string
  label: string
  kind?: 'box' | 'actor' | 'value' | 'decision' | 'output'
  narration_anchor?: string
}

export interface FlowVisual {
  type: 'flow'
  layout?: 'vertical' | 'horizontal' | 'branch'
  nodes: FlowNode[]
  edges?: { from: string; to: string; label?: string; narration_anchor?: string }[]
}

export interface VariableVisual {
  type: 'variable'
  variables: { name: string; value?: string; value_type?: string }[]
  steps?: { name: string; value: string; narration_anchor?: string }[]
}

export interface SequenceVisual {
  type: 'sequence'
  label?: string
  items: string[]
  pointer_label?: string
  steps?: { index: number; emit?: string; narration_anchor?: string }[]
}

export interface PracticeVisual {
  type: 'practice'
  prompt: string
}

export type Visual =
  | TextVisual | CodeEditorVisual | TerminalVisual
  | FlowVisual | VariableVisual | SequenceVisual | PracticeVisual

export interface Scene {
  beat: Beat
  narration_anchor?: string
  min_duration?: number
  visual: Visual
}

export interface Meal {
  schema_version: string
  id: string
  title: string
  concept: string
  objective: string
  scenes: Scene[]
  voice: { script: string; voice_id?: string }
  captions?: { enabled?: boolean; words_per_line?: number; highlight_active_word?: boolean }
  render?: { width?: number; height?: number; fps?: number }
}

/** The sidecar narrate.py produces: real timings from forced alignment. */
export interface Timing {
  meal_id: string
  audio: string
  duration: number
  alignment: 'whisper' | 'estimated'
  anchors: Record<string, number>
  captions: {
    text: string
    start: number
    end: number
    words: { w: string; start: number; end: number }[]
  }[]
}
