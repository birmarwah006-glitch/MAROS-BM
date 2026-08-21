/**
 * The single HTTP client for the MAROS backend.
 *
 * Auth mirrors frontend/api.js exactly — same localStorage/sessionStorage
 * keys — so a student can move between the old /app frontend and this one
 * without being logged out.
 *
 * Paths are RELATIVE on purpose. In dev, vite.config.ts proxies the API
 * prefixes to 127.0.0.1:8000; in production FastAPI serves this app from its
 * own origin. Either way the browser never makes a cross-origin request, so
 * CORS_ORIGINS on the backend needs no change.
 */

import type {
  ChatMessage,
  ChatRequest,
  ClassworkResponse,
  ClipperReel,
  CodeExecResult,
  Job,
  LectureSummary,
  Manifest,
  MasteryResponse,
  MealSummary,
  MealTiming,
  Module,
  NextConceptsResponse,
  Quiz,
  QuizSubmitAnswer,
  QuizSubmitResult,
  Reel,
  YouTubeInfo,
} from './types'

const SESSION_KEY = 'maros_session'
const PROF_TOKEN_KEY = 'maros_prof_token'

/** Distinguishes a backend error from a network failure, and keeps the
 *  server's own `detail` string so the UI can show a real reason. */
export class ApiError extends Error {
  constructor(
    readonly status: number,
    readonly detail: string,
    readonly path: string,
  ) {
    super(detail)
    this.name = 'ApiError'
  }

  /** 503 = the feature is switched off server-side (missing token/config).
   *  502 = it is configured but the upstream is unreachable. Different copy. */
  get isNotConfigured() {
    return this.status === 503
  }

  get isUpstreamDown() {
    return this.status === 502
  }
}

export function getSession(): { access_token?: string } | null {
  try {
    return JSON.parse(localStorage.getItem(SESSION_KEY) ?? 'null')
  } catch {
    return null
  }
}

export function isLoggedIn() {
  return Boolean(getSession()?.access_token)
}

function authHeaders(json = true): Record<string, string> {
  const headers: Record<string, string> = json
    ? { 'Content-Type': 'application/json' }
    : {}
  const token = getSession()?.access_token
  if (token) headers.Authorization = `Bearer ${token}`
  const prof = sessionStorage.getItem(PROF_TOKEN_KEY)
  if (prof) headers['X-Prof-Token'] = prof
  return headers
}

async function readError(res: Response, path: string): Promise<ApiError> {
  let detail = res.statusText || `Request failed (${res.status})`
  try {
    const body = await res.json()
    if (typeof body?.detail === 'string') detail = body.detail
    else if (body?.detail) detail = JSON.stringify(body.detail)
  } catch {
    /* non-JSON error body — keep the status text */
  }
  return new ApiError(res.status, detail, path)
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  let res: Response
  try {
    res = await fetch(path, init)
  } catch (cause) {
    throw new ApiError(
      0,
      'Could not reach the MAROS server. Is the backend running on port 8000?',
      path,
    )
  }
  if (!res.ok) throw await readError(res, path)
  if (res.status === 204) return undefined as T
  return (await res.json()) as T
}

function getJson<T>(path: string) {
  return request<T>(path, { headers: authHeaders(false) })
}

function postJson<T>(path: string, body: unknown) {
  return request<T>(path, {
    method: 'POST',
    headers: authHeaders(),
    body: JSON.stringify(body),
  })
}

/* ── Media URLs ────────────────────────────────────────────────────────────
   Returned as plain strings for <video>/<audio> src. These stream with HTTP
   range support (verified: 206 Partial Content), so seeking works. Auth is
   not attached — the backend does not gate media, and an <video> tag cannot
   send custom headers anyway. */

export const mediaUrl = {
  moduleVideo: (jobId: string, moduleId: number) =>
    `/modules/${encodeURIComponent(jobId)}/${moduleId}/video`,
  reelVideo: (jobId: string, moduleId: number) =>
    `/reels/${encodeURIComponent(jobId)}/${moduleId}/video`,
  reelAudio: (jobId: string, moduleId: number) =>
    `/reels/${encodeURIComponent(jobId)}/${moduleId}/audio`,
  clipperVideo: (jobId: string, moduleId: number) =>
    `/clipper/reels/${encodeURIComponent(jobId)}/${moduleId}/video`,
  mealVideo: (mealId: string) => `/meals/${encodeURIComponent(mealId)}/video`,
}

/* ── Lectures & modules ─────────────────────────────────────────────────── */

export const api = {
  health: () => getJson<{ system: string; status: string; version: string }>('/'),

  lectures: () => getJson<LectureSummary[]>('/lectures'),

  modules: (jobId: string) =>
    getJson<Module[]>(`/modules/${encodeURIComponent(jobId)}`),

  moduleNotes: (jobId: string, moduleId: number) =>
    getJson<{ module_id: number; notes: string }>(
      `/modules/${encodeURIComponent(jobId)}/${moduleId}/notes`,
    ),

  youtubeInfo: (jobId: string) =>
    getJson<YouTubeInfo>(`/jobs/${encodeURIComponent(jobId)}/youtube`),

  /* ── Jobs ─────────────────────────────────────────────────────────────── */

  /** Accepted content types are enforced server-side; see ACCEPTED_UPLOAD_TYPES. */
  uploadLecture: (file: File) => {
    const fd = new FormData()
    fd.append('file', file)
    return request<Job>('/jobs', {
      method: 'POST',
      headers: authHeaders(false),
      body: fd,
    })
  },

  ingestYouTube: (url: string) => postJson<Job>('/jobs/youtube', { url }),

  /** NOTE: the job store is in-memory. This 404s after a server restart —
   *  callers must fall back to the manifest. See useJobProgress. */
  job: (jobId: string) => getJson<Job>(`/jobs/${encodeURIComponent(jobId)}`),

  /** Returns 202 (not an error) while the job is still processing. */
  manifest: (jobId: string) =>
    getJson<Manifest>(`/jobs/${encodeURIComponent(jobId)}/manifest`),

  /* ── Quiz ─────────────────────────────────────────────────────────────── */

  generateQuiz: (jobId: string, moduleId: number, numQuestions = 5) =>
    postJson<Quiz>('/quiz/generate', {
      job_id: jobId,
      module_id: moduleId,
      num_questions: numQuestions,
    }),

  submitQuiz: (jobId: string, moduleId: number, answers: QuizSubmitAnswer[]) =>
    postJson<QuizSubmitResult>('/quiz/submit', {
      job_id: jobId,
      module_id: moduleId,
      answers,
    }),

  /* ── Chat / tutor ─────────────────────────────────────────────────────── */

  chat: (req: ChatRequest) =>
    postJson<ChatMessage>('/chat', {
      role: 'student',
      mode: 'videos',
      history: [],
      ...req,
    }),

  executeCode: (language: string, code: string) =>
    postJson<CodeExecResult>('/chat/execute-code', { language, code }),

  /* ── Reels ────────────────────────────────────────────────────────────── */

  reels: (jobId: string) =>
    getJson<{ job_id: string; reels: Reel[] }>(
      `/reels/${encodeURIComponent(jobId)}`,
    ),

  reel: (jobId: string, moduleId: number) =>
    getJson<Reel>(`/reels/${encodeURIComponent(jobId)}/${moduleId}`),

  clipperReels: (jobId: string) =>
    getJson<ClipperReel[]>(`/clipper/reels/${encodeURIComponent(jobId)}`),

  /* ── Meals ────────────────────────────────────────────────────────────── */

  meals: () => getJson<{ meals: MealSummary[] }>('/meals'),

  mealTiming: (mealId: string) =>
    getJson<MealTiming>(`/meals/${encodeURIComponent(mealId)}/timing`),

  /* ── Student progress ─────────────────────────────────────────────────── */

  mastery: () => getJson<MasteryResponse>('/student/mastery'),
  classwork: () => getJson<ClassworkResponse>('/student/classwork'),
  nextConcepts: () => getJson<NextConceptsResponse>('/student/next'),
}

/** Exactly the set main.py accepts on POST /jobs. Kept in sync by hand;
 *  widening it here without widening it there produces a 400. */
export const ACCEPTED_UPLOAD_TYPES = [
  'video/mp4',
  'video/quicktime',
  'video/x-msvideo',
  'audio/mpeg',
  'audio/wav',
  'audio/x-wav',
] as const

/** Languages GLOT_LANG_MAP recognises in main.py. */
export const EXECUTABLE_LANGUAGES = [
  'python', 'javascript', 'typescript', 'java',
  'c', 'cpp', 'csharp', 'go', 'rust',
] as const
