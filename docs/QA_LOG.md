# DeckAgent Quality Assurance (QA) Log

## Run: Graph_hack

- **Repository**: `https://github.com/Aryapd021/Graph_hack`
- **Template ID**: `tmpl_1` (Default Template)
- **Date**: `2026-10-29`
- **Server Mode**: Production mode (`uvicorn app.main:app --workers 1 --port 8000`)

### 1. Generation Pipeline & Timings
- **Total Duration**: `58.08s`
- **Committed Version**: `v1` (Project `8f4fdd152e074f28a0325bcc83ae7463`)
- **Stage Timings**:
  - `ingest`: 10.39s (Cloned and analyzed 10 key repository files)
  - `redact`: 0.00s (Chat sanitized on upload)
  - `digest`: 12.47s (Extracted 3 architectural decisions from chat)
  - `facts`: 10.33s (Extracted facts for Graph_hack)
  - `deck`: 17.61s (Drafted canonical 10-slide presentation)
  - `diagram`: 1.88s (Rendered Graphviz DOT component graph to SVG and PNG)
  - `render`: 0.26s (Built PowerPoint `.pptx` via `render_deck`)
  - `preview`: 0.02s (Skipped in outline mode)
  - `done`: Final version committed atomically

### 2. Issues Found
- **Gemini Free-Tier Daily Quota Limit**: `gemini-3.6-flash` encountered `429 RESOURCE_EXHAUSTED` (20 reqs/day limit). Resolved by updating model configuration to `gemini-3.5-flash-lite`.

### 3. Fixes Made
- `.env`: Switched `GEMINI_MODEL=gemini-3.5-flash-lite` for reliable quota headroom and low latency.
- `backend/tests/e2e_generate.py`: Added per-stage elapsed time tracking for QA timing verification.

### 4. Deferred Items
- None. All generated fields, schemas, and endpoints conformed to `CONTRACT.md` specifications.

### 5. Bullet & Deck Audit Result
- **Slide 1 (Title)**: `title` and `subtitle` sourced from `repo` (`README.md`). Clean.
- **Slide 2 (Problem)**: Sourced from `chat` motivation framing (preventing knowledge silos, decision drift). Clean.
- **Slide 3 (Solution)**: Unified FalkorDB knowledge graph (`chat`) + MCP AI assistant (`repo` `README.md`). Clean.
- **Slide 4 (Features)**: All 5 bullet points correspond directly to the 5 core features in `README.md` (`who_owns`, `who_knows`, `trace_decision`, `check_decision_freshness`, `flag_for_review`). Evidence paths verified.
- **Slide 5 (Architecture)**: Diagram contains 5 nodes (`frontend`, `api`, `mcp`, `falkordb`, `gemini`) and 4 directed edges representing REST, stdio, Cypher, and LLM communication. Bullets cite `backend/pyproject.toml`.
- **Slide 6 (Tech Stack)**: FalkorDB, FastAPI, Google GenAI SDK, MCP, React/Vite with Tailwind and Cytoscape. Verified in `backend/pyproject.toml` and `web/package.json`.
- **Slides 7-9 (Demo, Impact, Roadmap)**: Correctly flagged as `needs_input` placeholders prefixed with `TODO: `.
- **Slide 10 (Closing)**: Thank you slide with subtitle from `README.md`.
- **Fit Limits**: Zero truncation warnings; character lengths conform to manifest bounds.

### 6. Voice Edit & Undo Test
- **Voice Command**: `"Shorten slide 3 and move demo up."`
- **Result (v2)**: Created version `v2`. Demonstration slide moved from position 7 (index 6) to position 6 (index 5); slide 3 bullets updated.
- **Undo Result (v3)**: Calling `/undo` restored version `v1` state in version `v3` with summary `"Undo: restored v1"`. Both operations passed.

### 7. PPTX Verification & Fallback Copy
- Downloaded `deck.pptx` (129,024 bytes) saved to `docs/fallback/Graph_hack.pptx`.
- Re-opened with `python-pptx` without repair prompts or errors; all 10 slides and diagram image shape verified.
