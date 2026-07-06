// api.js — all MAROS backend calls

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
// CHAT — PROF OAK (now authenticated + persisted server-side)
// ─────────────────────────────────────────────

export async function chatWithOak({ message, jobId, moduleId, paperId, history, role, mode }) {
  const res = await fetch(`${BASE}/chat`, {
    method  : 'POST',
    headers : authHeaders(),   // ← THE FIX: identity now travels with every chat
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
// PAPERS
// ─────────────────────────────────────────────

export async function assignPaper(file) {
  const fd = new FormData();
  fd.append('file', file);
  const res = await fetch(`${BASE}/papers`, { method: 'POST', body: fd });
  if (!res.ok) throw new Error(`Assign paper failed: ${res.status}`);
  return res.json();
}

export async function listPapers() {
  const res = await fetch(`${BASE}/papers`);
  if (!res.ok) throw new Error(`List papers failed: ${res.status}`);
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