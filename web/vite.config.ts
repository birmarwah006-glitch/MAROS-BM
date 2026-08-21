import { defineConfig, type Plugin } from 'vite'
import react from '@vitejs/plugin-react'
import path from 'node:path'
import fs from 'node:fs'

// The MAROS backend serves its API off the ROOT path space (/lectures,
// /modules, /jobs ...) with no /api prefix, and its CORS_ORIGINS is pinned to
// http://localhost:8000 — so a dev server on :5173 is not an allowed origin.
//
// Rather than change backend config, every API prefix is proxied here. The
// browser only ever talks to its own origin, so CORS never comes into play,
// and the same relative paths work unchanged in production where FastAPI
// serves the built assets itself.
const API_PREFIXES = [
  '/jobs', '/lectures', '/modules', '/quiz', '/quizzes', '/chat',
  '/student', '/reels', '/clipper', '/prep', '/papers',
  '/assignments', '/professor',
]

const BACKEND = process.env.MAROS_API ?? 'http://127.0.0.1:8000'

/**
 * Serves /meals/* in development.
 *
 * The Meal routes exist as a real, self-contained FastAPI router at
 * MAROS/meal_routes.py, but mounting it means editing main.py — which is the
 * user's working file with uncommitted changes in it. Rather than touch it,
 * this middleware serves the SAME shapes off the SAME files, so the frontend
 * is written against the real contract rather than a mock.
 *
 * Nothing here is fabricated: it reads meals/catalogue/*.json and streams the
 * actual rendered MP4s. When meal_routes.py is mounted, delete this plugin and
 * add '/meals' to API_PREFIXES above — the frontend needs no change.
 */
function mealsDevServer(): Plugin {
  const MEALS = path.resolve(__dirname, '..', 'meals')
  const CATALOGUE = path.join(MEALS, 'catalogue')
  const BUILD = path.join(MEALS, 'build')
  const OUT = path.join(MEALS, 'out')

  const readMeal = (id: string) => {
    const p = path.join(CATALOGUE, `${id}.json`)
    if (!fs.existsSync(p)) return null
    return JSON.parse(fs.readFileSync(p, 'utf8')).meal
  }

  const duration = (id: string) => {
    const p = path.join(BUILD, `${id}.timing.json`)
    if (!fs.existsSync(p)) return null
    try {
      return JSON.parse(fs.readFileSync(p, 'utf8')).duration
    } catch {
      return null
    }
  }

  const summary = (meal: Record<string, unknown>) => ({
    id: meal.id,
    title: meal.title,
    concept: meal.concept,
    objective: meal.objective,
    difficulty: meal.difficulty ?? null,
    prerequisites: meal.prerequisites ?? [],
    next_concepts: meal.next_concepts ?? [],
    practice: meal.practice ?? null,
    video_url: `/meals/${meal.id}/video`,
    timing_url: `/meals/${meal.id}/timing`,
    duration_sec: duration(String(meal.id)),
  })

  const sendJson = (res: import('node:http').ServerResponse, body: unknown) => {
    res.setHeader('Content-Type', 'application/json')
    res.end(JSON.stringify(body))
  }

  /** Range-aware so <video> can seek, exactly as FileResponse does. */
  const sendFile = (
    req: import('node:http').IncomingMessage,
    res: import('node:http').ServerResponse,
    file: string,
    type: string,
  ) => {
    const { size } = fs.statSync(file)
    const range = req.headers.range
    res.setHeader('Content-Type', type)
    res.setHeader('Accept-Ranges', 'bytes')

    if (range) {
      const [rawStart, rawEnd] = range.replace('bytes=', '').split('-')
      const start = Number(rawStart)
      const end = rawEnd ? Number(rawEnd) : size - 1
      res.statusCode = 206
      res.setHeader('Content-Range', `bytes ${start}-${end}/${size}`)
      res.setHeader('Content-Length', end - start + 1)
      fs.createReadStream(file, { start, end }).pipe(res)
      return
    }

    res.setHeader('Content-Length', size)
    fs.createReadStream(file).pipe(res)
  }

  return {
    name: 'maros-meals-dev',
    configureServer(server) {
      server.middlewares.use((req, res, next) => {
        const url = (req.url ?? '').split('?')[0]
        if (!url.startsWith('/meals')) return next()

        const rest = url.slice('/meals'.length).replace(/^\//, '')

        if (rest === '') {
          if (!fs.existsSync(CATALOGUE)) return sendJson(res, { meals: [] })
          const meals = fs
            .readdirSync(CATALOGUE)
            .filter((f) => f.endsWith('.json'))
            .sort()
            .map((f) => {
              try {
                return JSON.parse(fs.readFileSync(path.join(CATALOGUE, f), 'utf8')).meal
              } catch {
                return null
              }
            })
            // Only Meals that have actually been rendered are listed: the
            // feed's contract is that everything in it is watchable.
            .filter((m) => m && fs.existsSync(path.join(OUT, `${m.id}.mp4`)))
            .map(summary)
          return sendJson(res, { meals })
        }

        const [id, kind] = rest.split('/')

        if (kind === 'video') {
          const file = path.join(OUT, `${id}.mp4`)
          if (!fs.existsSync(file)) {
            res.statusCode = 404
            return sendJson(res, { detail: 'Meal video not rendered yet.' })
          }
          return sendFile(req, res, file, 'video/mp4')
        }

        if (kind === 'audio') {
          const file = path.join(BUILD, `${id}.mp3`)
          if (!fs.existsSync(file)) {
            res.statusCode = 404
            return sendJson(res, { detail: 'Meal narration not found.' })
          }
          return sendFile(req, res, file, 'audio/mpeg')
        }

        if (kind === 'timing') {
          const file = path.join(BUILD, `${id}.timing.json`)
          if (!fs.existsSync(file)) {
            res.statusCode = 404
            return sendJson(res, { detail: 'Timing sidecar not found.' })
          }
          return sendJson(res, JSON.parse(fs.readFileSync(file, 'utf8')))
        }

        if (!kind) {
          const meal = readMeal(id)
          if (!meal) {
            res.statusCode = 404
            return sendJson(res, { detail: `Meal ${id} not found.` })
          }
          return sendJson(res, meal)
        }

        return next()
      })
    },
  }
}

export default defineConfig({
  plugins: [react(), mealsDevServer()],
  resolve: {
    alias: { '@': path.resolve(__dirname, 'src') },
  },
  server: {
    port: 5173,
    proxy: Object.fromEntries(
      API_PREFIXES.map((p) => [p, { target: BACKEND, changeOrigin: true }]),
    ),
  },
  build: {
    outDir: 'dist',
  },
})
