/**
 * Response shapes for the MAROS FastAPI backend.
 *
 * These were transcribed from models.py and then VERIFIED against a running
 * server — not inferred. Where the live response disagreed with the Python
 * type hints, the live response wins and the difference is commented.
 *
 * Nothing in this file describes an endpoint that does not exist. Missing
 * capability lives in docs/frontend-api-gaps.md, never here.
 */

export type JobStatus =
  | 'queued'
  | 'transcribing'
  | 'segmenting'
  | 'cutting'
  | 'summarizing'
  | 'done'
  | 'failed'

export interface Job {
  job_id: string
  status: JobStatus
  progress: number
  error: string | null
  created_at: string
}

export interface Module {
  module_id: number
  concept: string
  /** "MM:SS" or "HH:MM:SS" — a display string, not a number. */
  start: string
  end: string
  duration_sec: number
  video_url: string
  notes: string
  transcript: string
}

export interface Manifest {
  job_id: string
  video_source: string
  total_modules: number
  modules: Module[]
  generated_at: string
}

/** GET /lectures — a lighter shape than Manifest, and the restart-safe one. */
export interface LectureSummary {
  job_id: string
  title: string
  total_modules: number
  generated_at: string | null
  source: 'upload' | 'youtube'
  modules: Array<{ module_id: number; concept: string }>
}

export interface YouTubeInfo {
  source: 'upload' | 'youtube'
  url?: string
  video_id?: string
  title?: string
}

export interface QuizQuestion {
  question: string
  options: Record<string, string>
  correct_answer: string
  explanation: string
  /** Live responses return this as a string despite the `int` hint. */
  module_id: number | string
}

export interface Quiz {
  quiz_id: string
  module_id: number | string
  topic: string
  questions: QuizQuestion[]
  generated_at: string
}

export interface QuizSubmitAnswer {
  question_text: string
  options: Record<string, string>
  chosen_answer: string
  correct_answer: string
  concept_id?: string | null
}

export interface QuizSubmitResult {
  total: number
  correct: number
  score: number
  misconceptions: Array<{
    question?: string
    misconception?: string
    root_concept?: string
    reasoning?: string
    [k: string]: unknown
  }>
}

export interface ChatMessage {
  role: 'user' | 'assistant'
  content: string
  module_id?: number | null
  timestamp?: string | null
}

export type ChatMode = 'videos' | 'papers' | 'assignments' | 'prep'

export interface ChatRequest {
  message: string
  job_id?: string | null
  module_id?: number | null
  paper_id?: string | null
  history?: ChatMessage[]
  role?: 'student' | 'professor'
  mode?: ChatMode
}

export interface CodeExecResult {
  stdout: string
  stderr: string
  exit_code: number
}

/** Caption timings carry word-level granularity. Verified live. */
export interface ReelCaption {
  text: string
  start: number
  end: number
  words?: Array<{ w: string; start: number; end: number }>
}

export interface ReelPlan {
  concept: string
  concept_id: string
  why_this_one: string
  hook_angle: string
  exam_type: string
  pyq: { text?: string; source_label?: string; concept?: string } | null
  pyq_candidates: number
  beats: unknown
  script: string
  common_mistake: string
  word_count: number
}

export type ReelStatus = 'not_started' | 'generating' | 'done' | 'failed'

export interface Reel {
  job_id: string
  module_id: number
  module_concept?: string
  reel_status: ReelStatus
  reel_error?: string
  plan?: ReelPlan
  captions?: ReelCaption[]
  duration_sec?: number
  generated_at?: string
}

/** GET /clipper/reels/{job_id}. `thumbnail_url` is hardcoded null server-side. */
export interface ClipperReel {
  module_id: number
  concept: string
  video_url: string
  thumbnail_url: string | null
}

/**
 * The /student/* endpoints return 200 with an explanatory `message` when the
 * caller is anonymous, rather than 401. Modelled explicitly so callers render
 * the message instead of treating it as an error.
 */
export interface MasteryResponse {
  student_id?: string
  mastery: Array<{ concept_name: string; score: number; [k: string]: unknown }>
  message?: string
}

export interface ClassworkResponse {
  quizzes: Array<{
    module_id: string | null
    taken_at: string | null
    total: number
    correct: number
    score_pct: number
    questions: Array<{
      question: string | null
      chosen: string | null
      correct_answer: string | null
      is_correct: boolean | null
      misconception: string | null
    }>
  }>
  message?: string
}

export interface NextConceptsResponse {
  student_id?: string
  next_concepts?: Array<Record<string, unknown>>
  concepts?: Array<Record<string, unknown>>
  message?: string
}

/* ── Meals ────────────────────────────────────────────────────────────────
   MAROS's native short-form learning format. Shapes mirror meal_routes.py.
   Note the vocabulary split: the API and these types keep the backend's own
   naming, and only rendered strings ever say "Meal" to a learner. Here they
   agree, because /meals is a new surface with no legacy callers.            */

export interface MealPractice {
  prompt: string
  kind?: 'write_code' | 'predict_output' | 'fix_bug'
  starter_code?: string
  expected_stdout?: string
  hint?: string
}

export interface MealSummary {
  id: string
  title: string
  concept: string
  objective: string
  difficulty: string | null
  prerequisites: string[]
  next_concepts: string[]
  practice: MealPractice | null
  video_url: string
  timing_url: string
  duration_sec: number | null
}

export interface MealTiming {
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
