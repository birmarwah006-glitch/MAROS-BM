/**
 * Editable code block plus a terminal, backed by POST /chat/execute-code
 * (a server-side proxy to glot.io).
 *
 * Deliberately NOT Monaco. Per decision D3 nothing in V1 needs a full editor,
 * and a textarea over a syntax-highlighted layer is a fraction of the weight.
 *
 * Two failure modes are distinguished because the backend distinguishes them:
 *   503 — GLOT_API_TOKEN is not set: the feature is off on this server.
 *   502 — glot.io is unreachable: it is on, but the upstream is down.
 * Flattening those into "something went wrong" would leave the user with no
 * idea whether to wait or to tell someone.
 */

import { useState } from 'react'
import { useExecuteCode } from '@/api/hooks'
import { ApiError, EXECUTABLE_LANGUAGES } from '@/api/client'
import { Badge, Button, Spinner } from '@/components/ui'
import { cn } from '@/lib/cn'

export function CodeRunner({
  initialCode,
  language = 'python',
  title = 'Try it',
}: {
  initialCode: string
  language?: string
  title?: string
}) {
  const [code, setCode] = useState(initialCode)
  const [lang, setLang] = useState(language)
  const run = useExecuteCode()

  const result = run.data
  const error = run.error instanceof ApiError ? run.error : null

  const lineCount = code.split('\n').length

  return (
    <div className="overflow-hidden rounded border border-line bg-surface">
      <header className="flex items-center justify-between gap-3 border-b border-line px-4 py-2.5">
        <div className="flex items-center gap-2.5">
          <span className="flex gap-1.5" aria-hidden>
            <span className="h-2.5 w-2.5 rounded-full bg-line2" />
            <span className="h-2.5 w-2.5 rounded-full bg-line2" />
            <span className="h-2.5 w-2.5 rounded-full bg-line2" />
          </span>
          <span className="font-mono text-micro uppercase tracking-[0.1em] text-muted2">
            {title}
          </span>
        </div>

        <select
          value={lang}
          onChange={(e) => setLang(e.target.value)}
          className="rounded border border-line bg-surface2 px-2 py-1 font-mono text-micro text-muted2 focus:border-line2"
          aria-label="Language"
        >
          {EXECUTABLE_LANGUAGES.map((l) => (
            <option key={l} value={l}>
              {l}
            </option>
          ))}
        </select>
      </header>

      <div className="relative flex bg-surface2">
        <div
          aria-hidden
          className="select-none border-r border-line px-3 py-4 text-right font-mono text-xs leading-[1.6] text-muted"
        >
          {Array.from({ length: lineCount }, (_, i) => (
            <div key={i}>{i + 1}</div>
          ))}
        </div>
        <textarea
          value={code}
          onChange={(e) => setCode(e.target.value)}
          spellCheck={false}
          rows={Math.max(4, Math.min(lineCount + 1, 24))}
          className="flex-1 resize-y bg-transparent px-4 py-4 font-mono text-xs leading-[1.6] text-ink outline-none"
          aria-label="Code editor"
        />
      </div>

      <div className="flex items-center gap-3 border-t border-line px-4 py-3">
        <Button
          size="sm"
          variant="primary"
          onClick={() => run.mutate({ language: lang, code })}
          disabled={run.isPending}
        >
          {run.isPending ? <><Spinner /> Running</> : 'Run'}
        </Button>
        <Button
          size="sm"
          variant="ghost"
          onClick={() => {
            setCode(initialCode)
            run.reset()
          }}
        >
          Reset
        </Button>
        {result && (
          <Badge tone={result.exit_code === 0 ? 'green' : 'red'}>
            exit {result.exit_code}
          </Badge>
        )}
      </div>

      {(result || error) && (
        <div className="border-t border-line">
          <p className="border-b border-line px-4 py-2 font-mono text-micro uppercase tracking-[0.1em] text-muted2">
            Output
          </p>
          <pre
            className={cn(
              'max-h-64 overflow-auto px-4 py-3 font-mono text-xs leading-relaxed',
              error ? 'text-orange' : 'text-ink',
            )}
          >
            {error
              ? error.isNotConfigured
                ? `Code execution is not configured on this MAROS server.\n\n${error.detail}`
                : error.isUpstreamDown
                  ? `The code execution service could not be reached.\n\n${error.detail}`
                  : error.detail
              : [result?.stdout, result?.stderr].filter(Boolean).join('\n') ||
                '(no output)'}
          </pre>
        </div>
      )}
    </div>
  )
}
