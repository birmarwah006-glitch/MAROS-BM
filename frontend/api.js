// api.js — all MAROS backend calls  (v4)

// ── BASE: localhost in dev, same-origin in prod (frontend served by FastAPI /app mount) ──
export const BASE =
  (location.hostname === 'localhost' || location.hostname === '127.0.0.1')
    ? 'http://localhost:8000'
    : location.origin;   // works when Render serves frontend + backend together

// ── Auth: attach the student's Supabase token to EVERY request ──
export function authHeaders(json = true) {
  const s = JSON.parse(localStorage.getItem('maros_session') || 'null');
  const h = json ? { 'Content-Type': 'application/json' } : {};
  if (s?.access_token) h['Authorization'] = `Bearer ${s.access_token}`;
  const profToken = sessionStorage.getItem('maros_prof_token');
  if (profToken) h['X-Prof-Token'] = profToken;
  return h;
}

// ─────────────────────────────────────────────
// JOBS
// ─────────────────────────────────────────────

export async function submitJob(file) {
  const fd = new FormData();
  fd.append('file', file);
  const res = await fetch(`${BASE}/jobs`, { method: 'POST', body: fd });
  if (!res.ok) throw new Error(`Submit failed: ${res.status}`);
  return res.json();
}

export async function pollJob(jobId) {
  const res = await fetch(`${BASE}/jobs/${jobId}`);
  if (!res.ok) throw new Error(`Poll failed: ${res.status}`);
  return res.json();
}

export async function getManifest(jobId) {
  const res = await fetch(`${BASE}/jobs/${jobId}/manifest`);
  if (!res.ok) throw new Error(`Manifest failed: ${res.status}`);
  return res.json();
}

// ─────────────────────────────────────────────
// MODULES
// ─────────────────────────────────────────────

export async function getModules(jobId) {
  const res = await fetch(`${BASE}/modules/${jobId}`);
  if (!res.ok) throw new Error(`Modules failed: ${res.status}`);
  return res.json();
}

export function getVideoUrl(jobId, moduleId) {
  return `${BASE}/modules/${jobId}/${moduleId}/video`;
}

export async function getModuleNotes(jobId, moduleId) {
  const res = await fetch(`${BASE}/modules/${jobId}/${moduleId}/notes`);
  if (!res.ok) throw new Error(`Notes failed: ${res.status}`);
  return res.json();
}

// ─────────────────────────────────────────────
// QUIZ
// ─────────────────────────────────────────────

export async function generateQuiz(jobId, moduleId, numQuestions = 5) {
  const res = await fetch(`${BASE}/quiz/generate`, {
    method  : 'POST',
    headers : authHeaders(),
    body    : JSON.stringify({
      job_id        : jobId,
      module_id     : moduleId,
      num_questions : numQuestions
    })
  });
  if (!res.ok) throw new Error(`Quiz failed: ${res.status}`);
  return res.json();
}

// ─────────────────────────────────────────────
// PROFESSOR QUIZ (v4) — PDF → parsed → published
// ─────────────────────────────────────────────

export async function parseQuizPdf(file) {
  const fd = new FormData();
  fd.append('file', file);
  const res = await fetch(`${BASE}/professor/quiz/parse-pdf`, { method: 'POST', body: fd });
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new Error(err.detail || `Parse failed: ${res.status}`);
  }
  return res.json();
}

export async function publishProfQuiz(title, questions) {
  const res = await fetch(`${BASE}/professor/quiz/publish`, {
    method  : 'POST',
    headers : { 'Content-Type': 'application/json' },
    body    : JSON.stringify({ title, questions })
  });
  if (!res.ok) throw new Error(`Publish failed: ${res.status}`);
  return res.json();
}

export async function listProfQuizzes(visibleOnly = false) {
  const res = await fetch(`${BASE}/professor/quizzes?visible_only=${visibleOnly}`);
  if (!res.ok) throw new Error(`List quizzes failed: ${res.status}`);
  return res.json();
}

// Students: visible prof quizzes
export async function listStudentQuizzes() {
  const res = await fetch(`${BASE}/quizzes`);
  if (!res.ok) throw new Error(`List quizzes failed: ${res.status}`);
  return res.json();
}

// Student submits a prof-published quiz taken inside Oak chat.
// answers: [{ question_index, chosen_answer }]
export async function submitProfQuiz(quizId, answers) {
  const res = await fetch(`${BASE}/professor/quiz/${quizId}/submit`, {
    method  : 'POST',
    headers : authHeaders(),          // student auth → analytics gets student_id
    body    : JSON.stringify({ answers })
  });
  if (!res.ok) throw new Error(`Submit prof quiz failed: ${res.status}`);
  return res.json();
}

// ─────────────────────────────────────────────
// MODULE QUIZ REVIEW (v4)
// ─────────────────────────────────────────────

export async function reviewModuleQuiz(jobId, moduleId, numQuestions = 5) {
  const res = await fetch(`${BASE}/professor/module-quiz/review`, {
    method  : 'POST',
    headers : { 'Content-Type': 'application/json' },
    body    : JSON.stringify({ job_id: jobId, module_id: moduleId, num_questions: numQuestions })
  });
  if (!res.ok) throw new Error(`Review draft failed: ${res.status}`);
  return res.json();
}

export async function publishModuleQuiz(jobId, moduleId, topic, questions) {
  const res = await fetch(`${BASE}/professor/module-quiz/publish`, {
    method  : 'POST',
    headers : { 'Content-Type': 'application/json' },
    body    : JSON.stringify({ job_id: jobId, module_id: moduleId, topic, questions })
  });
  if (!res.ok) throw new Error(`Publish failed: ${res.status}`);
  return res.json();
}

// ─────────────────────────────────────────────
// OAK QUESTION ANALYTICS (v4)
// ─────────────────────────────────────────────

export async function getOakQuestionAnalytics() {
  const res = await fetch(`${BASE}/professor/oak-questions`);
  if (!res.ok) throw new Error(`Oak analytics failed: ${res.status}`);
  return res.json();
}

// ─────────────────────────────────────────────
// CHAT — PROF OAK (now authenticated + persisted server-side)
// ─────────────────────────────────────────────

export async function chatWithOak({ message, jobId, moduleId, paperId, history, role, mode }) {
  const res = await fetch(`${BASE}/chat`, {
    method  : 'POST',
    headers : authHeaders(),
    body    : JSON.stringify({
      message   : message,
      job_id    : jobId    || null,
      module_id : moduleId || null,
      paper_id  : paperId  || null,
      history   : history  || [],
      role      : role     || 'student',
      mode      : mode     || 'videos'
    })
  });
  if (!res.ok) throw new Error(`Chat failed: ${res.status}`);
  return res.json();
}

// Restore saved chat threads: { threads: { videos: [...], papers: [...], ... } }
export async function getChatHistory(scope = 'student') {
  const res = await fetch(`${BASE}/chat/history?scope=${scope}`, {
    headers: authHeaders()
  });
  if (!res.ok) return { threads: {} };
  return res.json();
}

// ─────────────────────────────────────────────
// PAPERS  (v4: auth travels with upload/list — student podcasts stay per-student)
// ─────────────────────────────────────────────

export async function assignPaper(file) {
  const fd = new FormData();
  fd.append('file', file);
  const res = await fetch(`${BASE}/papers`, {
    method  : 'POST',
    headers : authHeaders(false),   // v4: owner identity — student uploads stay private
    body    : fd
  });
  if (!res.ok) throw new Error(`Assign paper failed: ${res.status}`);
  return res.json();
}

export async function listPapers() {
  const res = await fetch(`${BASE}/papers`, { headers: authHeaders() });
  if (!res.ok) throw new Error(`List papers failed: ${res.status}`);
  return res.json();
}

export async function deletePaper(paperId) {
  const res = await fetch(`${BASE}/papers/${paperId}`, {
    method  : 'DELETE',
    headers : authHeaders()
  });
  if (!res.ok) throw new Error(`Delete paper failed: ${res.status}`);
  return res.json();
}

export async function getPaper(paperId) {
  const res = await fetch(`${BASE}/papers/${paperId}`);
  if (!res.ok) throw new Error(`Get paper failed: ${res.status}`);
  return res.json();
}

export async function generatePodcast(paperId) {
  const res = await fetch(`${BASE}/papers/${paperId}/podcast`, { method: 'POST' });
  if (!res.ok) throw new Error(`Generate podcast failed: ${res.status}`);
  return res.json();
}

export async function getPodcastStatus(paperId) {
  const res = await fetch(`${BASE}/papers/${paperId}/podcast`);
  if (!res.ok) throw new Error(`Podcast status failed: ${res.status}`);
  return res.json();
}

export function getPodcastAudioUrl(paperId) {
  return `${BASE}/papers/${paperId}/podcast/audio`;
}

// ─────────────────────────────────────────────
// STATE
// ─────────────────────────────────────────────

export const STATE = {
  jobId    : localStorage.getItem('maros_job_id') || null,
  modules  : [],
  activeModule : null,

  setJobId(id) {
    this.jobId = id;
    localStorage.setItem('maros_job_id', id);
  },
  setModules(modules) { this.modules = modules; },
  setActiveModule(moduleId) { this.activeModule = moduleId; },
  clear() {
    this.jobId = null;
    this.modules = [];
    this.activeModule = null;
    localStorage.removeItem('maros_job_id');
  }
};