# MAROS Meals

A **Meal** is MAROS's native short-form learning format: one learning
objective, told as hook → question → concept → visual → code → execution →
takeaway → practice, with voice, visuals, real code and captions.

This directory holds the Meal *content* and the Python tooling. The renderer
that turns a Meal into an MP4 lives in `../meal-renderer/`.

## Build a Meal

```bash
# 1. validate the document (schema + rules a schema cannot express)
python3 validate.py catalogue/meal_input_output.json

# 2. ACTUALLY run the code and record its real output
python3 verify.py catalogue/meal_input_output.json

# 3. synthesise the voice and forced-align it to get beat timings
../venv/bin/python narrate.py catalogue/meal_input_output.json

# 4. render 1080x1920 @ 30fps
cd ../meal-renderer && node render.mjs ../meals/catalogue/meal_input_output.json
```

Output lands in `meals/out/{id}.mp4`.

## The rule that matters most

**Never claim that code executed if it did not.**

`verify.py` executes each snippet for real and stamps the result with
`verified: true`, the executor, and a timestamp. `validate.py` rejects any Meal
with an unverified terminal scene, and the renderer throws rather than draw
one. Terminal output is a recorded capture, never authored text.

## Layout

```
schema/meal.schema.json   the canonical AI -> renderer contract
catalogue/*.json          Meals (hand-authored today; planner-generated later)
validate.py               schema + semantic validation
verify.py                 real code execution, writes back the recorded output
narrate.py                TTS + Whisper forced alignment -> timing sidecar
build/                    generated audio and timing sidecars
out/                      rendered Meals
```

## Adding a Meal

Write a JSON file in `catalogue/`. Nothing else. The renderer is data-driven —
one renderer, many Meal files — so a new Meal needs no renderer changes unless
it needs a visual type that does not exist yet.

Beat timing is never hand-authored. Each scene carries a `narration_anchor`: a
verbatim phrase from `voice.script`. Forced alignment finds when that phrase is
actually spoken, and the beat starts there. Visuals cannot drift out of sync
with the voice.
