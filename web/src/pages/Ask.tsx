/**
 * Ask MAROS, standalone.
 *
 * POST /chat without job_id/module_id. In "videos" mode the backend grounds
 * the answer in its RAG corpus (OS exam papers) — see main.py. Optionally
 * scoped to a lecture module, which is the same grounding the lesson page
 * uses.
 */

import { useState } from 'react'
import { useLectures, useModules } from '@/api/hooks'
import { Container } from '@/components/ui'
import { TutorPanel } from '@/components/TutorPanel'

export default function Ask() {
  const { data: lectures } = useLectures()
  const [jobId, setJobId] = useState<string>('')
  const { data: modules } = useModules(jobId || undefined)
  const [moduleId, setModuleId] = useState<string>('')

  const concept = modules?.find((m) => String(m.module_id) === moduleId)?.concept

  return (
    <Container>
      <div className="mx-auto max-w-3xl py-10 md:py-14">
        <header>
          <p className="eyebrow">Ask MAROS</p>
          <h1 className="mt-3 font-display text-[1.75rem] font-extrabold tracking-[-0.03em] text-ink sm:text-3xl">
            Your tutor, on the material you are studying
          </h1>
          <p className="mt-3 text-base text-muted2">
            Ask anything. Point it at a specific concept and it will answer
            against that lecture's transcript.
          </p>
        </header>

        {(lectures?.length ?? 0) > 0 && (
          <div className="mt-8 flex flex-wrap gap-3">
            <label className="flex-1 min-w-[12rem]">
              <span className="eyebrow">Lecture</span>
              <select
                value={jobId}
                onChange={(e) => { setJobId(e.target.value); setModuleId('') }}
                className="mt-2 w-full rounded border border-line bg-surface2 px-3 py-2 text-sm text-ink focus:border-line2 focus:outline-none"
              >
                <option value="">Any (general questions)</option>
                {lectures!.map((l) => (
                  <option key={l.job_id} value={l.job_id}>
                    {l.title}
                  </option>
                ))}
              </select>
            </label>

            {jobId && (
              <label className="flex-1 min-w-[12rem]">
                <span className="eyebrow">Concept</span>
                <select
                  value={moduleId}
                  onChange={(e) => setModuleId(e.target.value)}
                  className="mt-2 w-full rounded border border-line bg-surface2 px-3 py-2 text-sm text-ink focus:border-line2 focus:outline-none"
                >
                  <option value="">Whole lecture</option>
                  {modules?.map((m) => (
                    <option key={m.module_id} value={m.module_id}>
                      {String(m.module_id).padStart(2, '0')} — {m.concept}
                    </option>
                  ))}
                </select>
              </label>
            )}
          </div>
        )}

        <TutorPanel
          key={`${jobId}-${moduleId}`}
          className="mt-8 min-h-[26rem]"
          jobId={jobId || undefined}
          moduleId={moduleId ? Number(moduleId) : undefined}
          concept={concept}
        />
      </div>
    </Container>
  )
}
