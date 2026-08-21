/**
 * Module quiz.
 *
 * POST /quiz/generate returns a published professor quiz if one exists for
 * the module, otherwise generates one from the transcript. POST /quiz/submit
 * scores it, runs LLM misconception diagnosis on the wrong answers, and
 * updates mastery — but only for a logged-in student; anonymous submissions
 * still score, they just are not tracked. That distinction is shown, not
 * hidden.
 */

import { useState } from 'react'
import { useGenerateQuiz } from '@/api/hooks'
import { api, ApiError, isLoggedIn } from '@/api/client'
import type { Quiz as QuizType, QuizSubmitResult } from '@/api/types'
import { Badge, Button, ErrorState, Spinner } from '@/components/ui'
import { cn } from '@/lib/cn'

export function Quiz({ jobId, moduleId }: { jobId: string; moduleId: number }) {
  const generate = useGenerateQuiz()
  const [quiz, setQuiz] = useState<QuizType | null>(null)
  const [answers, setAnswers] = useState<Record<number, string>>({})
  const [result, setResult] = useState<QuizSubmitResult | null>(null)
  const [submitting, setSubmitting] = useState(false)
  const [submitError, setSubmitError] = useState<string | null>(null)

  async function start() {
    setResult(null)
    setAnswers({})
    setSubmitError(null)
    const q = await generate.mutateAsync({ jobId, moduleId })
    setQuiz(q)
  }

  async function submit() {
    if (!quiz) return
    setSubmitting(true)
    setSubmitError(null)
    try {
      const payload = quiz.questions.map((q, i) => ({
        question_text: q.question,
        options: q.options,
        chosen_answer: answers[i] ?? '',
        correct_answer: q.correct_answer,
      }))
      setResult(await api.submitQuiz(jobId, moduleId, payload))
    } catch (err) {
      setSubmitError(
        err instanceof ApiError ? err.detail : 'Could not submit the quiz.',
      )
    } finally {
      setSubmitting(false)
    }
  }

  if (!quiz) {
    return (
      <div className="rounded border border-line bg-surface p-6">
        <p className="font-display text-base font-semibold text-ink">
          Check what stuck
        </p>
        <p className="mt-1.5 max-w-lg text-sm text-muted2">
          Questions are written against this specific concept, from the
          transcript it came from.
        </p>
        <Button
          variant="primary"
          className="mt-5"
          onClick={() => void start()}
          disabled={generate.isPending}
        >
          {generate.isPending ? <><Spinner /> Writing questions</> : 'Start the quiz'}
        </Button>
        {generate.isError && (
          <ErrorState
            title="Could not generate a quiz"
            detail={
              generate.error instanceof ApiError
                ? generate.error.detail
                : String(generate.error)
            }
            onRetry={() => void start()}
          />
        )}
      </div>
    )
  }

  const answered = Object.keys(answers).length
  const total = quiz.questions.length

  return (
    <div data-testid="quiz" className="rounded border border-line bg-surface">
      <header className="flex flex-wrap items-center justify-between gap-3 border-b border-line px-5 py-4">
        <div>
          <p className="font-display text-base font-semibold text-ink">
            {quiz.topic}
          </p>
          <p className="mt-0.5 font-mono text-micro text-muted">
            {total} QUESTIONS
          </p>
        </div>
        {result ? (
          <span data-testid="quiz-score"><Badge tone={result.score >= 0.6 ? 'green' : 'orange'}>
            {result.correct}/{result.total} correct
          </Badge></span>
        ) : (
          <span className="font-mono text-micro text-muted">
            {answered}/{total} ANSWERED
          </span>
        )}
      </header>

      <ol className="divide-y divide-line">
        {quiz.questions.map((q, i) => {
          const chosen = answers[i]
          return (
            <li key={i} data-testid="quiz-question" className="px-5 py-5">
              <p className="font-display text-sm font-medium leading-relaxed text-ink">
                <span className="mr-2 font-mono text-micro text-muted">
                  {String(i + 1).padStart(2, '0')}
                </span>
                {q.question}
              </p>

              <div className="mt-4 grid gap-2">
                {Object.entries(q.options).map(([key, label]) => {
                  const isChosen = chosen === key
                  const isCorrect = q.correct_answer === key
                  const graded = Boolean(result)

                  return (
                    <button
                      key={key}
                      type="button"
                      disabled={graded}
                      onClick={() => setAnswers((a) => ({ ...a, [i]: key }))}
                      className={cn(
                        'flex items-start gap-3 rounded border px-3.5 py-2.5 text-left text-sm transition-colors',
                        graded && isCorrect &&
                          'border-green-line bg-green-soft text-ink',
                        graded && isChosen && !isCorrect &&
                          'border-red-line bg-red-soft text-ink',
                        graded && !isCorrect && !isChosen &&
                          'border-line text-muted',
                        !graded && isChosen &&
                          'border-green-line bg-green-soft text-ink',
                        !graded && !isChosen &&
                          'border-line text-muted2 hover:border-line2 hover:text-ink',
                      )}
                    >
                      <span className="font-mono text-micro leading-5 text-muted">
                        {key}
                      </span>
                      <span className="flex-1">{label}</span>
                    </button>
                  )
                })}
              </div>

              {result && (
                <div className="mt-3 rounded border border-line bg-surface2 px-3.5 py-3">
                  <p className="eyebrow mb-1.5">Why</p>
                  <p className="whitespace-pre-wrap text-xs leading-relaxed text-muted2">
                    {q.explanation}
                  </p>
                </div>
              )}
            </li>
          )
        })}
      </ol>

      <footer className="border-t border-line px-5 py-4">
        {submitError && <ErrorState detail={submitError} />}

        {!result ? (
          <div className="flex flex-wrap items-center gap-3">
            <Button
              variant="primary"
              onClick={() => void submit()}
              disabled={answered < total || submitting}
            >
              {submitting ? <><Spinner /> Marking</> : 'Submit answers'}
            </Button>
            {answered < total && (
              <span className="text-xs text-muted">
                {total - answered} left to answer
              </span>
            )}
            {!isLoggedIn() && (
              <span className="text-xs text-muted">
                Not signed in — this attempt will be scored but not saved.
              </span>
            )}
          </div>
        ) : (
          <div>
            {result.misconceptions.length > 0 && (
              <div className="mb-4">
                <p className="eyebrow mb-2">What tripped you up</p>
                <ul className="space-y-2">
                  {result.misconceptions.map((m, i) => (
                    <li
                      key={i}
                      className="rounded border border-line bg-surface2 px-3.5 py-3 text-xs leading-relaxed text-muted2"
                    >
                      {m.misconception ?? m.reasoning ?? JSON.stringify(m)}
                    </li>
                  ))}
                </ul>
              </div>
            )}
            <Button variant="secondary" onClick={() => void start()}>
              Try a new set of questions
            </Button>
          </div>
        )}
      </footer>
    </div>
  )
}
