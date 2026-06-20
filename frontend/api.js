 // api.js — all MAROS backend calls

const BASE = 'http://localhost:8000';

// ─────────────────────────────────────────────
// JOBS
// ─────────────────────────────────────────────

export async function submitJob(file) {
  const fd = new FormData();
  fd.append('file', file);

  const res = await fetch(`${BASE}/jobs`, {
    method : 'POST',
    body   : fd
  });

  if (!res.ok) throw new Error(`Submit failed: ${res.status}`);
  return res.json();   // { job_id, status, progress, ... }
}

export async function pollJob(jobId) {
  const res = await fetch(`${BASE}/jobs/${jobId}`);
  if (!res.ok) throw new Error(`Poll failed: ${res.status}`);
  return res.json();   // { job_id, status, progress, error }
}

export async function getManifest(jobId) {
  const res = await fetch(`${BASE}/jobs/${jobId}/manifest`);
  if (!res.ok) throw new Error(`Manifest failed: ${res.status}`);
  return res.json();   // { job_id, modules: [...] }
}

// ─────────────────────────────────────────────
// MODULES
// ─────────────────────────────────────────────

export async function getModules(jobId) {
  const res = await fetch(`${BASE}/modules/${jobId}`);
  if (!res.ok) throw new Error(`Modules failed: ${res.status}`);
  return res.json();   // [ { module_id, concept, ... } ]
}

export function getVideoUrl(jobId, moduleId) {
  return `${BASE}/modules/${jobId}/${moduleId}/video`;
}

export async function getModuleNotes(jobId, moduleId) {
  const res = await fetch(`${BASE}/modules/${jobId}/${moduleId}/notes`);
  if (!res.ok) throw new Error(`Notes failed: ${res.status}`);
  return res.json();   // { module_id, notes }
}

// ─────────────────────────────────────────────
// QUIZ
// ─────────────────────────────────────────────

export async function generateQuiz(jobId, moduleId, numQuestions = 5) {
  const res = await fetch(`${BASE}/quiz/generate`, {
    method  : 'POST',
    headers : { 'Content-Type': 'application/json' },
    body    : JSON.stringify({
      job_id        : jobId,
      module_id     : moduleId,
      num_questions : numQuestions
    })
  });

  if (!res.ok) throw new Error(`Quiz failed: ${res.status}`);
  return res.json();   // { quiz_id, topic, questions: [...] }
}

// ─────────────────────────────────────────────
// CHAT — PROF OAK
// ─────────────────────────────────────────────

export async function chatWithOak({ message, jobId, moduleId, history, role }) {
  const res = await fetch(`${BASE}/chat`, {
    method  : 'POST',
    headers : { 'Content-Type': 'application/json' },
    body    : JSON.stringify({
      message   : message,
      job_id    : jobId   || null,
      module_id : moduleId || null,
      history   : history  || [],
      role      : role     || 'student'
    })
  });

  if (!res.ok) throw new Error(`Chat failed: ${res.status}`);
  return res.json();   // { role, content, module_id, timestamp }
}

// ─────────────────────────────────────────────
// SHARED STATE
// (job_id shared between prof and student screens)
// ─────────────────────────────────────────────

export const STATE = {
  jobId    : localStorage.getItem('maros_job_id') || null,
  modules  : [],
  activeModule : null,

  setJobId(id) {
    this.jobId = id;
    localStorage.setItem('maros_job_id', id);
  },

  setModules(modules) {
    this.modules = modules;
  },

  setActiveModule(moduleId) {
    this.activeModule = moduleId;
  },

  clear() {
    this.jobId = null;
    this.modules = [];
    this.activeModule = null;
    localStorage.removeItem('maros_job_id');
  }
};

