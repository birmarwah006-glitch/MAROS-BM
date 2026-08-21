/**
 * Add a lecture.
 *
 * Only the input methods the backend genuinely supports are offered:
 *
 *   POST /jobs          multipart upload, content-type restricted server-side
 *   POST /jobs/youtube  {url}
 *
 * The brief also lists "paste a course URL". There is NO endpoint for that
 * (documented in frontend-api-gaps.md), so it is not offered here. Showing a
 * third input that silently did nothing, or faked a result, would be worse
 * than leaving it out.
 */

import { useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { useIngestYouTube, useUploadLecture } from '@/api/hooks'
import { ACCEPTED_UPLOAD_TYPES, ApiError } from '@/api/client'
import { Button, Container, ErrorState, Spinner } from '@/components/ui'
import { cn } from '@/lib/cn'

type Mode = 'youtube' | 'upload'

const MAX_HINT = 'MP4, MOV, AVI, MP3 or WAV'

export default function AddLecture() {
  const navigate = useNavigate()
  const [mode, setMode] = useState<Mode>('youtube')
  const [url, setUrl] = useState('')
  const [file, setFile] = useState<File | null>(null)
  const [dragging, setDragging] = useState(false)
  const [localError, setLocalError] = useState<string | null>(null)
  const inputRef = useRef<HTMLInputElement>(null)

  const upload = useUploadLecture()
  const ingest = useIngestYouTube()
  const busy = upload.isPending || ingest.isPending

  const activeError = upload.error ?? ingest.error
  const errorDetail =
    activeError instanceof ApiError ? activeError.detail : activeError ? String(activeError) : null

  function chooseFile(next: File | null) {
    setLocalError(null)
    if (!next) return setFile(null)
    if (!(ACCEPTED_UPLOAD_TYPES as readonly string[]).includes(next.type)) {
      setLocalError(
        `MAROS cannot process "${next.type || next.name}". Use ${MAX_HINT}.`,
      )
      setFile(null)
      return
    }
    setFile(next)
  }

  async function submit() {
    setLocalError(null)
    try {
      const job =
        mode === 'youtube'
          ? await ingest.mutateAsync(url.trim())
          : await upload.mutateAsync(file!)
      navigate(`/processing/${job.job_id}`)
    } catch {
      /* surfaced through errorDetail */
    }
  }

  const canSubmit =
    mode === 'youtube' ? /^https?:\/\/\S+$/.test(url.trim()) : Boolean(file)

  return (
    <Container>
      <div className="mx-auto max-w-2xl py-10 md:py-16">
        <header>
          <p className="eyebrow">Add a lecture</p>
          <h1 className="mt-3 font-display text-[1.75rem] font-extrabold leading-[1.15] tracking-[-0.03em] text-ink sm:text-3xl">
            Learn from your own lecture
          </h1>
          <p className="mt-3 text-base text-muted2">
            Give MAROS a recording and it will transcribe it, find the concepts
            it teaches, and build a lesson for each one.
          </p>
        </header>

        <div className="mt-9 flex gap-2">
          {(['youtube', 'upload'] as const).map((m) => (
            <button
              key={m}
              type="button"
              onClick={() => { setMode(m); setLocalError(null) }}
              className={cn(
                'rounded border px-3.5 py-2 text-sm transition-colors',
                mode === m
                  ? 'border-green-line bg-green-soft text-green'
                  : 'border-line2 text-muted2 hover:text-ink',
              )}
            >
              {m === 'youtube' ? 'Paste a YouTube link' : 'Upload a file'}
            </button>
          ))}
        </div>

        <div className="mt-6">
          {mode === 'youtube' ? (
            <div>
              <label htmlFor="yt" className="eyebrow">
                YouTube URL
              </label>
              <input
                id="yt"
                value={url}
                onChange={(e) => setUrl(e.target.value)}
                placeholder="https://youtu.be/..."
                inputMode="url"
                className="mt-2 w-full rounded border border-line bg-surface2 px-4 py-3 font-mono text-sm text-ink placeholder:text-muted focus:border-line2 focus:outline-none"
              />
              <p className="mt-2 text-xs text-muted">
                MAROS downloads the audio only. You will still watch the video
                on YouTube.
              </p>
            </div>
          ) : (
            <div
              onDragOver={(e) => { e.preventDefault(); setDragging(true) }}
              onDragLeave={() => setDragging(false)}
              onDrop={(e) => {
                e.preventDefault()
                setDragging(false)
                chooseFile(e.dataTransfer.files?.[0] ?? null)
              }}
              onClick={() => inputRef.current?.click()}
              className={cn(
                'flex cursor-pointer flex-col items-center justify-center rounded border border-dashed px-6 py-12 text-center transition-colors',
                dragging ? 'border-green bg-green-soft' : 'border-line2 hover:border-muted2',
              )}
            >
              <input
                ref={inputRef}
                type="file"
                className="hidden"
                accept={ACCEPTED_UPLOAD_TYPES.join(',')}
                onChange={(e) => chooseFile(e.target.files?.[0] ?? null)}
              />
              {file ? (
                <>
                  <p className="font-display text-sm font-semibold text-ink">
                    {file.name}
                  </p>
                  <p className="mt-1 font-mono text-micro text-muted">
                    {(file.size / 1024 / 1024).toFixed(1)} MB · click to change
                  </p>
                </>
              ) : (
                <>
                  <p className="font-display text-sm font-semibold text-ink">
                    Drop a lecture recording here
                  </p>
                  <p className="mt-1 text-xs text-muted">{MAX_HINT}</p>
                </>
              )}
            </div>
          )}
        </div>

        {(localError || errorDetail) && (
          <div className="mt-5">
            <ErrorState
              title={
                mode === 'youtube'
                  ? 'Could not start that lecture'
                  : 'Could not upload that file'
              }
              detail={localError ?? errorDetail ?? undefined}
            />
          </div>
        )}

        <Button
          variant="primary"
          size="lg"
          className="mt-7"
          disabled={!canSubmit || busy}
          onClick={() => void submit()}
        >
          {busy ? <><Spinner /> Starting</> : 'Analyse this lecture'}
        </Button>

        <div className="mt-12 border-t border-line pt-6">
          <p className="eyebrow mb-3">What happens next</p>
          <ol className="space-y-2 text-sm text-muted2">
            <li>1. The audio is transcribed locally.</li>
            <li>2. The transcript is segmented into the concepts it teaches.</li>
            <li>3. Each concept gets notes, a concept map, and its own clip.</li>
            <li>4. Quizzes are written from the material itself.</li>
          </ol>
          <p className="mt-4 text-xs text-muted">
            A full-length lecture takes a while — the page will keep you posted.
          </p>
        </div>
      </div>
    </Container>
  )
}
