/**
 * TanStack Query bindings over the API client.
 *
 * Two backend realities shape everything here:
 *   1. The job store is in-memory, so GET /jobs/{id} 404s after a restart.
 *   2. Long jobs run as FastAPI BackgroundTasks with no push channel, so
 *      progress is polled.
 */

import {
  useMutation,
  useQuery,
  useQueryClient,
  type UseQueryOptions,
} from '@tanstack/react-query'
import { api, ApiError, isLoggedIn } from './client'
import type { Job, JobStatus, Manifest } from './types'

export const queryKeys = {
  lectures: ['lectures'] as const,
  modules: (jobId: string) => ['modules', jobId] as const,
  notes: (jobId: string, moduleId: number) =>
    ['notes', jobId, moduleId] as const,
  youtube: (jobId: string) => ['youtube', jobId] as const,
  reels: (jobId: string) => ['reels', jobId] as const,
  job: (jobId: string) => ['job', jobId] as const,
  manifest: (jobId: string) => ['manifest', jobId] as const,
  meals: ['meals'] as const,
  mealTiming: (id: string) => ['meal-timing', id] as const,
  mastery: ['mastery'] as const,
  classwork: ['classwork'] as const,
}

/** Retrying a 4xx just burns time — the answer will not change. */
function retryServerErrorsOnly(failureCount: number, error: unknown) {
  if (error instanceof ApiError && error.status >= 400 && error.status < 500) {
    return false
  }
  return failureCount < 2
}

const baseOptions = {
  retry: retryServerErrorsOnly,
  staleTime: 30_000,
} satisfies Partial<UseQueryOptions>

export function useLectures() {
  return useQuery({
    ...baseOptions,
    queryKey: queryKeys.lectures,
    queryFn: api.lectures,
  })
}

export function useModules(jobId: string | undefined) {
  return useQuery({
    ...baseOptions,
    queryKey: queryKeys.modules(jobId ?? ''),
    queryFn: () => api.modules(jobId!),
    enabled: Boolean(jobId),
  })
}

export function useModuleNotes(jobId: string | undefined, moduleId: number | undefined) {
  return useQuery({
    ...baseOptions,
    queryKey: queryKeys.notes(jobId ?? '', moduleId ?? -1),
    queryFn: () => api.moduleNotes(jobId!, moduleId!),
    enabled: Boolean(jobId) && moduleId !== undefined,
  })
}

export function useYouTubeInfo(jobId: string | undefined) {
  return useQuery({
    ...baseOptions,
    queryKey: queryKeys.youtube(jobId ?? ''),
    queryFn: () => api.youtubeInfo(jobId!),
    enabled: Boolean(jobId),
    staleTime: Infinity,
  })
}

export function useReels(jobId: string | undefined) {
  return useQuery({
    ...baseOptions,
    queryKey: queryKeys.reels(jobId ?? ''),
    queryFn: () => api.reels(jobId!),
    enabled: Boolean(jobId),
  })
}

/* ── Progress tracking ─────────────────────────────────────────────────── */

export interface JobProgress {
  status: JobStatus | 'unknown'
  progress: number
  error: string | null
  manifest: Manifest | null
  /** True when the in-memory job record is gone but the work may still be
   *  real — the server restarted, or the job predates it. */
  recoveredFromDisk: boolean
  isSettled: boolean
}

const STAGE_ORDER: JobStatus[] = [
  'queued', 'transcribing', 'segmenting', 'cutting', 'summarizing', 'done',
]

export const STAGE_LABELS: Record<JobStatus, string> = {
  queued: 'Queued',
  transcribing: 'Transcribing the lecture',
  segmenting: 'Extracting concepts',
  cutting: 'Cutting concept clips',
  summarizing: 'Writing notes and concept maps',
  done: 'Ready',
  failed: 'Failed',
}

export function stageIndex(status: JobStatus | 'unknown') {
  const i = STAGE_ORDER.indexOf(status as JobStatus)
  return i === -1 ? 0 : i
}
export const TOTAL_STAGES = STAGE_ORDER.length - 1

/**
 * Poll a processing job, surviving the in-memory job store.
 *
 * IMPORTANT: /jobs/{id}/manifest is NOT disk-backed. It looks the job up in
 * the same in-memory store first (main.py get_manifest), so after a restart
 * BOTH /jobs/{id} and /jobs/{id}/manifest return 404 even though
 * outputs/{id}/manifest.json exists. Verified live.
 *
 * The genuinely restart-safe endpoints are /modules/{id} and /lectures, which
 * read the manifest straight off disk. So the chain is:
 *
 *   GET /jobs/{id}            live status while the process is up
 *     -> 404 or done
 *   GET /jobs/{id}/manifest   202 while working, 200 when finished
 *     -> 404
 *   GET /modules/{id}         disk-backed; if it returns modules, the job
 *                             finished and the record was simply lost
 *
 * A 404 therefore means "status unknown", never "failed" — losing the record
 * during a 20-minute transcription is normal in this deployment.
 */
export function useJobProgress(jobId: string | undefined) {
  return useQuery<JobProgress>({
    queryKey: ['job-progress', jobId],
    enabled: Boolean(jobId),
    retry: false,
    refetchInterval: (query) =>
      query.state.data?.isSettled ? false : 2_500,
    queryFn: async (): Promise<JobProgress> => {
      let job: Job | null = null
      let recoveredFromDisk = false

      try {
        job = await api.job(jobId!)
      } catch (error) {
        if (!(error instanceof ApiError && error.status === 404)) throw error
        recoveredFromDisk = true
      }

      if (job && job.status !== 'done') {
        return {
          status: job.status,
          progress: job.progress,
          error: job.error,
          manifest: null,
          recoveredFromDisk,
          isSettled: job.status === 'failed',
        }
      }

      // Either the job says done, or its record is gone. Both are answered
      // by the manifest, which is written to disk and survives restarts.
      try {
        const manifest = await api.manifest(jobId!)
        return {
          status: 'done',
          progress: 100,
          error: null,
          manifest,
          recoveredFromDisk,
          isSettled: true,
        }
      } catch (error) {
        if (error instanceof ApiError && error.status === 202) {
          return {
            status: job?.status ?? 'unknown',
            progress: job?.progress ?? 0,
            error: null,
            manifest: null,
            recoveredFromDisk,
            isSettled: false,
          }
        }
        if (error instanceof ApiError && error.status === 404) {
          // Last resort: the disk-backed module list. If it answers, the work
          // is genuinely finished and only the job record was lost.
          try {
            const modules = await api.modules(jobId!)
            if (modules.length > 0) {
              return {
                status: 'done',
                progress: 100,
                error: null,
                manifest: {
                  job_id: jobId!,
                  video_source: '',
                  total_modules: modules.length,
                  modules,
                  generated_at: '',
                },
                recoveredFromDisk: true,
                isSettled: true,
              }
            }
          } catch {
            /* not on disk either — genuinely still unknown */
          }

          return {
            status: job?.status ?? 'unknown',
            progress: job?.progress ?? 0,
            error: null,
            manifest: null,
            recoveredFromDisk: true,
            isSettled: false,
          }
        }
        throw error
      }
    },
  })
}

export function useMeals() {
  return useQuery({
    ...baseOptions,
    queryKey: queryKeys.meals,
    queryFn: api.meals,
  })
}

/** Caption track for a Meal. Word-level timings come from forced alignment. */
export function useMealTiming(mealId: string | undefined) {
  return useQuery({
    ...baseOptions,
    queryKey: queryKeys.mealTiming(mealId ?? ''),
    queryFn: () => api.mealTiming(mealId!),
    enabled: Boolean(mealId),
    staleTime: Infinity,
  })
}

/* ── Mutations ─────────────────────────────────────────────────────────── */

export function useUploadLecture() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (file: File) => api.uploadLecture(file),
    onSuccess: () => qc.invalidateQueries({ queryKey: queryKeys.lectures }),
  })
}

export function useIngestYouTube() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (url: string) => api.ingestYouTube(url),
    onSuccess: () => qc.invalidateQueries({ queryKey: queryKeys.lectures }),
  })
}

export function useGenerateQuiz() {
  return useMutation({
    mutationFn: ({ jobId, moduleId, count }: {
      jobId: string; moduleId: number; count?: number
    }) => api.generateQuiz(jobId, moduleId, count ?? 5),
  })
}

export function useExecuteCode() {
  return useMutation({
    mutationFn: ({ language, code }: { language: string; code: string }) =>
      api.executeCode(language, code),
  })
}

/* ── Student data (200-with-message when logged out) ───────────────────── */

export function useMastery() {
  return useQuery({
    ...baseOptions,
    queryKey: queryKeys.mastery,
    queryFn: api.mastery,
  })
}

export function useClasswork() {
  return useQuery({
    ...baseOptions,
    queryKey: queryKeys.classwork,
    queryFn: api.classwork,
    enabled: isLoggedIn(),
  })
}
