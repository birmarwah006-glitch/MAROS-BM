# MAROS Meal renderer

Turns a Meal JSON document into a 1080x1920 30fps MP4.

**One renderer, many Meal files.** Nothing here knows about `input()`, for
loops, or any particular Meal — it reads the document and animates what it
finds. Adding a Meal means adding JSON, not code.

```bash
node render.mjs ../meals/catalogue/meal_input_output.json
```

Requires `meals/narrate.py` to have produced the audio and timing sidecar
first.

## Stack, and why

| Choice | Reason |
|---|---|
| **Motion Canvas** (MIT) | Programmatic, deterministic animation. Its built-in `Code` node ships syntax highlighting and typing/diff animation. |
| **Monaco — deliberately NOT used here** | Monaco is a DOM editor; Motion Canvas renders to a canvas. They cannot share a scene graph. Monaco lives in `web/`'s practice panel, where a learner actually types. |
| **Remotion — rejected** | Not open source. Free only for individuals and companies of 3 or fewer employees, and it forbids derivative rendering products. Unsuitable for MAROS. |
| **FFmpeg** | Final encode, and the audio mux. |
| **Playwright** | Headless Chromium host. Motion Canvas 3.17 has no headless render CLI. |

## How the headless render works

Motion Canvas's FFmpeg exporter runs inside the editor UI. Rather than robot
that UI, this uses the same public API the editor uses — `Renderer` plus a
custom `Exporter` whose `handleFrame` streams each PNG out to Node, which pipes
them straight into FFmpeg. No frames touch disk.

See `docs/meal-architecture.md` for the non-obvious details (the plugin's CJS
interop, the `?project` suffix, the dep-cache bust) and for the known
Motion Canvas text-layout limitation.

## Layout

```
src/scene.tsx              the data-driven builder: one beat -> one visual
src/components/            visual primitives (spec section 34)
src/theme.ts               MAROS tokens, mirrored from frontend/style.css
src/main.ts                headless entry: Renderer + frame exporter
render.mjs                 Vite + Chromium + FFmpeg driver
```
