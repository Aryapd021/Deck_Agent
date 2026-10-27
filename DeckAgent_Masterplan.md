# DeckAgent Masterplan: 25 Hours, 5 Days

**Ground rules**
- [ ] The architecture doc is law: no stack changes, no new features.
- [ ] Each chunk has a fallback. If a chunk overruns by 50%, take the fallback and move on.
- [ ] The last 15 to 30 minutes of every day is the exit test plus `git commit` and `git tag dayN`.
- [ ] Day 5 has no new features.

---

## 1. Hour 0 Setup Checklist (before the clock starts)

### 1.1 Prerequisites
- [ ] Python 3.11+, Node 20+, Git, and Graphviz are installed (`dot -V`, `git --version`, `node -v`, `python --version`).
- [ ] LibreOffice is installed (optional but recommended): `soffice --version`. On Windows, add its `program` folder to `PATH`.
- [ ] You have one LLM API key (Gemini or Anthropic) and have made one successful test call.
- [ ] You have your real hackathon `.pptx` template ready.
- [ ] You have picked 3 test repos (old, varied: e.g. one JS, one Python, one mixed). Also have one real LLM chat export saved as text.
- [ ] Wispr Flow is installed and tested by dictating into any text field.

### 1.2 Repo and folders
```bash
mkdir deckagent && cd deckagent
git init
mkdir -p backend/app/{models,api,core,services/llm,prompts} backend/tests/fixtures templates storage
touch backend/app/__init__.py backend/app/{models,api,core,services,services/llm}/__init__.py
touch backend/app/{main,config,db}.py
touch backend/app/models/{deck,facts,edit_ops,api,tables}.py
touch backend/app/api/{templates,projects,jobs,deck,artifacts}.py
touch backend/app/core/{jobs,errors,storage}.py
touch backend/app/services/llm/{base,gemini,anthropic}.py
touch backend/app/services/{ingest,chat_digest,extract,diagram,render,preview,edit,template_inspector,validate}.py
touch README.md .env.example
```

### 1.3 `.gitignore` and `.env`
```bash
printf ".env\nstorage/\n.venv/\n__pycache__/\nnode_modules/\nfrontend/dist/\n*.pyc\n" > .gitignore
cat > .env.example << 'EOF'
LLM_PROVIDER=gemini
GEMINI_API_KEY=
ANTHROPIC_API_KEY=
GITHUB_TOKEN=
STORAGE_DIR=./storage
PREVIEW_MODE=libreoffice
MAX_REPO_TOKENS=12000
MAX_SLIDES=10
EOF
cp .env.example .env     # then paste your real key into .env
```
- [ ] `.env` is listed in `.gitignore` (verify with `git status`).

### 1.4 Backend environment
```bash
cd backend
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
```
Create `backend/pyproject.toml` with these dependencies: `fastapi`, `uvicorn[standard]`, `pydantic>=2`, `pydantic-settings`, `sqlmodel`, `python-pptx`, `pymupdf`, `python-multipart`, `httpx`, and the SDK for your active provider (`google-genai` or `anthropic`). Dev dependency: `pytest`.
```bash
pip install -e ".[dev]"
cd ..
```
- [ ] `python -c "import fastapi, pptx, fitz, sqlmodel"` runs without errors.

### 1.5 Frontend
```bash
npm create vite@latest frontend -- --template react-ts
cd frontend
npm install
npm install @tanstack/react-query
npm install -D tailwindcss @tailwindcss/vite openapi-typescript
mkdir -p src/api src/components
```
- [ ] In `vite.config.ts`: add the `@tailwindcss/vite` plugin and `server.proxy: { '/api': 'http://localhost:8000' }`.
- [ ] Add `@import "tailwindcss";` to `src/index.css`.
- [ ] Add these scripts to `package.json`:
  ```json
  "gen:api": "openapi-typescript http://localhost:8000/openapi.json -o src/api/schema.d.ts",
  "check:api": "npm run gen:api && git diff --exit-code src/api/schema.d.ts"
  ```
- [ ] `npm run dev` shows the Vite page. Then `cd ..`.

### 1.6 Dev run commands (two terminals)
- [ ] Terminal A: `cd backend && uvicorn app.main:app --reload --workers 1 --port 8000`
- [ ] Terminal B: `cd frontend && npm run dev`
- [ ] Copy your template into `templates/_source/` for use as the Day 1 upload.
- [ ] First commit: `git add -A && git commit -m "hour 0 scaffold"`

---

## 2. Day-by-Day Execution Plan

### Day 1: Contract, template, render, wiring (5h)

- [ ] **0:00–0:30: Config, DB, errors, storage helpers**
  - Files: `config.py`, `db.py`, `core/errors.py`, `core/storage.py`
  - `Settings` reads `.env`. The engine and `create_all` are in place. The error codes are an enum plus the `{code, message, detail}` envelope with an exception handler. `storage.py` has versioned `pathlib` helpers (`version_dir(project_id, n)`, temp-dir-then-rename helper).
- [ ] **0:30–1:15: The contract models**
  - Files: `models/deck.py`, `models/facts.py`, `models/edit_ops.py`, `models/api.py`
  - `deck.py` holds Source, Field, SlideType, Slide, Deck. Enforce max 5 bullets with a validator here.
  - `facts.py` holds ProjectFacts, ComponentGraph (4 to 8 nodes, edges reference real nodes), ChatDigest, and RepoDigest, which Day 2 needs.
  - `edit_ops.py` holds the six ops as a discriminated union.
  - `api.py` holds `HealthResponse`, the error envelope, `JobEvent`, and request models. Doing this now means they appear in the OpenAPI schema.
- [ ] **1:15–1:45: Tables and app factory**
  - Files: `models/tables.py`, `main.py`
  - Tables: template, project, project_input, deck_version (unique `(project_id, version)`), job, job_event, artifact.
  - `main.py` has the app factory, an `/api` router, and `GET /api/health` that checks `dot`, `git`, and `soffice` via `shutil.which`. If `frontend/dist` exists, mount it last so it doesn't shadow `/api`.
- [ ] **1:45–2:45: Template inspector**
  - File: `services/template_inspector.py`
  - List layouts and placeholders (`idx`, type, name, position, size). Build `layout_map` by name heuristics and placeholder types. Validate and fall back to "Title and Content" with stored warnings. Derive per-placeholder char limits from placeholder size. Write `templates/<id>.manifest.json`.
  - Fallback: if heuristics eat time, hardcode a name-substring lookup table and move on.
- [ ] **2:45–3:15: Template endpoints**
  - File: `api/templates.py`
  - `POST /api/templates` (upload, inspect, save row, return manifest and warnings), `GET /api/templates`, `PUT /api/templates/{id}/layout-map`. Register the router in `main.py`.
- [ ] **3:15–4:15: Render from a hardcoded Deck**
  - Files: `services/render.py`, `backend/tests/fixtures/sample_deck.py`, `backend/tests/render_smoke.py`
  - Build a 10-slide sample Deck covering every SlideType. `render.py` reads only the manifest, fills placeholders, and writes speaker notes. Write to a temp dir, then atomic rename to `v<N>`. Image insertion is Day 3.
  - Run: `python -m tests.render_smoke <template_id>`.
- [ ] **4:15–4:45: Frontend wiring**
  - Files: `frontend/vite.config.ts`, `src/api/client.ts`, `src/api/schema.d.ts` (generated), `src/App.tsx`
  - With the backend running: `npm run gen:api`. `client.ts` is a typed fetch wrapper using `paths` from the generated schema. `App.tsx` shows `/api/health` through TanStack Query.
- [ ] **4:45–5:00: Exit test, commit, tag `day1`**

**Daily Exit Test (Day 1)**
- [ ] Upload your template via `POST /api/templates` (Swagger UI at `/docs` is fine). It returns a manifest, and any warnings are visible.
- [ ] `python -m tests.render_smoke` writes `storage/.../v1/deck.pptx` and **it opens in PowerPoint** with the real template layouts and all 10 slides populated.
- [ ] `curl localhost:8000/api/health` returns the `dot`/`git`/`soffice` flags.
- [ ] The browser at the Vite dev port shows health data, and `npm run build` passes TypeScript using the generated types.
- [ ] `npm run check:api` exits 0.

---

### Day 2: Ingest, LLM, extraction, jobs, SSE (5h)

- [ ] **0:00–0:45: LLM provider interface**
  - Files: `services/llm/base.py`, `gemini.py` or `anthropic.py` (the active one; the other is a stub that raises)
  - Define `generate_structured(prompt, schema: type[BaseModel]) -> BaseModel`. On `ValidationError`, retry once with the error text appended. A second failure raises `LLM_INVALID_OUTPUT`. All SDK calls go through `asyncio.to_thread` or the SDK's async client. Add a loader for `prompts/*.md` by name.
- [ ] **0:45–1:30: Repo ingest**
  - File: `services/ingest.py`
  - `git clone --depth 1` using subprocess list args (or accept a local path) into a temp dir. Build `RepoDigest` in priority order: README, manifests, top-level tree, entrypoints, route/model files. Enforce the ~12k token cap (a `len/4` estimate is fine). Skip `node_modules`, lockfiles, binaries, and build output. Map failures to `REPO_UNREACHABLE`.
- [ ] **1:30–2:00: Chat redaction and digest**
  - File: `services/chat_digest.py`
  - A `redact()` regex pass strips API keys, tokens, emails, and Bearer strings. `digest()` is the LLM call to `ChatDigest`.
- [ ] **2:00–3:00: Prompts, extract, validate**
  - Files: `prompts/facts.md`, `prompts/deck.md`, `services/extract.py`, `services/validate.py`
  - `extract_facts()` makes one LLM call to `ProjectFacts`. `build_deck()` makes one LLM call to `Deck`.
  - Prompts must state the source priority (`user` > `repo` > `voice` > `chat`), that chat supplies motivation and decisions only, and that unsupported claims become `needs_input`.
  - `validate.py` for now: structural checks, the fit-limit check function, and the source-priority rule. Auto-shorten is Day 4.
- [ ] **3:00–4:00: Job runner and SSE**
  - Files: `core/jobs.py`, `api/jobs.py`
  - A per-project `asyncio.Lock` with non-blocking acquire; a busy lock returns 409 `JOB_IN_PROGRESS`. `emit(job_id, stage, status, message)` writes to `job_event` first and then pushes to live subscribers. `GET /api/jobs/{job_id}/events` is a `StreamingResponse` with `text/event-stream`, an `id:` per event, replay from `Last-Event-ID`, and it closes on `done`/`error`.
- [ ] **4:00–4:30: Project and deck endpoints**
  - Files: `api/projects.py`, `api/deck.py`
  - `POST /api/projects`, `PUT /api/projects/{id}/inputs` (call `redact()` here so the raw chat is never persisted), `POST .../generate` (returns 202 and `job_id`), `GET .../deck`.
- [ ] **4:30–5:00: Wire the generate pipeline and run the exit test**
  - File: `core/jobs.py` (`run_generate`)
  - Chain stages: ingest → redact (emit from the stored text) → digest → facts → deck → validate → render → commit. Skip diagram and preview until Day 3. Commit the `deck_version` row only after render succeeds.

**Daily Exit Test (Day 2)**
- [ ] With curl or Swagger, create a project (one of your 3 test repos, a local path or URL, plus pitch notes plus the pasted chat). Call generate, and `curl -N` the events URL. You see `ingest → redact → digest → facts → deck → render → done` events.
- [ ] `GET /deck` returns JSON that passes `Deck.model_validate`. Every Field has a `source`, repo-sourced ones have `evidence` filenames, and at least one claim is flagged `needs_input` or chat-sourced.
- [ ] `storage/projects/<id>/v1/deck.pptx` opens in PowerPoint.
- [ ] Calling generate twice quickly returns 409 `JOB_IN_PROGRESS` on the second call.
- [ ] Open the events URL again with a `Last-Event-ID` header: only the later events replay.

---

### Day 3: Diagram, preview, UI, then the gate (5h)

- [ ] **0:00–0:45: Diagram**
  - File: `services/diagram.py`
  - `ComponentGraph` → DOT string with fixed styling (shape by `kind`, left-to-right, template palette) → `dot -Tsvg` and `-Tpng` via subprocess with list args → `diagram.svg` and `diagram.png` in the version dir.
  - Fallback: if parsing theme colors from the template takes more than 15 minutes, use a constant palette.
- [ ] **0:45–1:30: Image insertion**
  - Files: `services/render.py`, `services/template_inspector.py`
  - On slides with `image_ref == "diagram"`, insert the PNG into the picture placeholder, or into the content area from the manifest, preserving the aspect ratio.
- [ ] **1:30–2:00: Preview and artifacts**
  - Files: `services/preview.py`, `api/artifacts.py`
  - `soffice --headless --convert-to pdf` then PyMuPDF to `slides/N.png`. If `soffice` is missing or `PREVIEW_MODE=outline`, skip without error. Serve `GET .../v/{n}/slides/{k}.png` and `.../deck.pptx`. Record artifact rows.
- [ ] **2:00–2:30: Wire stages 6–8 and run the CHECKPOINT**
  - File: `core/jobs.py`
  - Run a full generate through curl on one test repo.
  - **Checkpoint rule:** if this isn't producing a pptx with a diagram by 2:30, the Feature Freeze triggers *now* (see Section 3). Don't wait for 5:00.
- [ ] **2:30–3:00: Frontend plumbing**
  - Files: `src/api/client.ts`, `src/main.tsx`
  - Run `npm run gen:api` again. Add the QueryClientProvider, an SSE hook (`EventSource`, with auto-refetch of `/deck` on `done`), and typed mutations for create, inputs, and generate.
- [ ] **3:00–4:30: Three-pane UI**
  - Files: `src/components/InputsPane.tsx`, `DeckPane.tsx`, `CommandPane.tsx`, `HealthBanner.tsx`, `src/App.tsx`
  - Inputs: repo URL/path, pitch textarea, chat textarea, template picker, Generate button.
  - Deck: PNG thumbnails if they exist, otherwise the outline view, with source badges (repo, voice, chat, you) and `needs_input` highlighted read-only for now. Include a diagram view and a download button.
  - Command pane: SSE progress list only (the command box is Day 4).
  - A health banner appears when a tool is missing.
- [ ] **4:30–5:00: End-to-end from the UI, then the gate decision**

**Daily Exit Test (Day 3)**
- [ ] In the browser, fill the inputs, click Generate, and watch live progress. The deck appears with the architecture diagram on the diagram slide, built from the actual repo.
- [ ] The downloaded `.pptx` opens in PowerPoint with the diagram in place.
- [ ] Refreshing the page mid-job loses no progress events.
- [ ] If any item above is false: **trigger the Feature Freeze and update the plan before logging off.**

---

### Day 4: Edit engine, undo, versions, needs_input (5h)

- [ ] **0:00–0:45: Interpret**
  - Files: `services/edit.py`, `prompts/edit_interpret.md`, `models/edit_ops.py`
  - Input: the command, the compact Deck JSON, and the selected slide. Output: a list of typed ops (schema-validated, with the same one-retry rule). Out-of-scope commands return an empty op list and a polite "I can't do that yet".
- [ ] **0:45–1:30: Validate and apply**
  - Files: `services/edit.py`, `services/validate.py`
  - Validate that every `slide_id` exists, indexes are in range, and content passes the fit limits. Any failure rejects the whole batch. Apply to a `deepcopy`, then bump `version`. Generate a one-line summary string.
  - Write unit tests for all six ops plus a rejection case.
- [ ] **1:30–2:15: Edit endpoint and job**
  - Files: `api/projects.py`, `core/jobs.py` (`run_edit`)
  - `POST .../edit` with a `base_version` check (409 `STALE_VERSION`) and the project lock. Re-render the pptx and previews. Rerun the diagram only for `SetDiagramFocus`; otherwise reuse the diagram file. Commit the new version with `created_by=edit` and the summary.
- [ ] **2:15–2:45: Undo and versions**
  - Files: `api/deck.py`
  - `GET .../versions`, `POST .../undo` (new version copied from the previous one, `created_by=undo`).
- [ ] **2:45–3:15: Fit-limit auto-shorten**
  - File: `services/validate.py`
  - On a limit breach: one LLM "shorten" call, then hard-truncate at a word boundary and flag. Hook it into both generate (after `deck`) and edit.
- [ ] **3:15–4:15: Command UI**
  - Files: `src/components/CommandPane.tsx`, `src/components/VersionBar.tsx`
  - Large textarea with the "Hold your Wispr key and speak" hint, Enter to send, disabled while a job runs, showing the op summary. The version bar has v1/v2/v3 chips, Undo, and Download. A 409 shows a clear message.
- [ ] **4:15–4:45: needs_input and manual PATCH**
  - Files: `api/deck.py`, `src/components/DeckPane.tsx`
  - `PATCH /api/projects/{id}/deck` (requires `base_version`, creates a version with `created_by=manual`). An inline edit field on `needs_input` bullets flips the source to `user`.
  - **Skip this chunk if the Feature Freeze was triggered.**
- [ ] **4:45–5:00: Exit test, commit, tag `day4`**

**Daily Exit Test (Day 4)**
- [ ] Generate a deck, then dictate with Wispr Flow into the command box: **"Shorten slide 3 and move demo up."** A new version appears. Slide 3 has fewer bullets, the Demo slide moved up, and the summary line says so.
- [ ] Click Undo: a new version appears that matches the previous one, and history is intact.
- [ ] Try each of the six ops once (update, move, delete, add, set diagram focus, regenerate). None crashes.
- [ ] Send an edit with a stale `base_version`: you get 409 `STALE_VERSION`.
- [ ] Send an invalid command like "make it blue": nothing changes and a polite message shows.
- [ ] The downloaded edited `.pptx` opens in PowerPoint.

---

### Day 5: Quality, record, submit (5h). **No new features.**

- [ ] **0:00–0:30: Production-mode check**
  - `cd frontend && npm run build`. Start `uvicorn app.main:app --workers 1` from `backend/`. Open `localhost:8000` and confirm the full flow works with no dev proxy and no console errors. Run `npm run check:api`.
- [ ] **0:30–2:15: Run all 3 test repos**
  - For each repo: generate, read every bullet against the repo, then edit by voice once. Log issues in a list and fix **only** bugs, prompt wording, and limits. Re-run after each fix. Keep a decent output of each repo's deck for the video fallback.
- [ ] **2:15–2:45: README and PowerPoint verification**
  - File: `README.md`
  - Install steps (Python, Node, Graphviz, LibreOffice), `.env` setup, `--workers 1` explained, and run commands. Open each of the 3 downloaded `.pptx` files in PowerPoint and confirm there are no repair prompts.
- [ ] **2:45–3:15: Demo script and rehearsal**
  - Do a timed run-through with the checklist in Section 4. Reset to a clean state.
- [ ] **3:15–4:00: Record the demo video** (see Section 4)
- [ ] **4:00–4:30: Write-up and usage card**
  - Write-up covers the problem, the approach (provenance, typed ops, deterministic rendering), and what you built. Take the usage card screenshot.
- [ ] **4:30–5:00: Submit**
  - Submit with plenty of buffer ahead of the **31 Oct, 11:59 pm IST** deadline. Do not wait for the last hour.

**Daily Exit Test (Day 5)**
- [ ] Every item in the architecture's Definition of Done is checked, or consciously noted as cut under the freeze rule.
- [ ] The submission confirmation is received.

---

## 3. Critical Path & Feature Freeze Triggers

### 3.1 The riskiest integration points
- [ ] **SSE and the job runner.** Persist every event to `job_event` *before* pushing it to subscribers, and test replay with `curl -N -H "Last-Event-ID: 3"` on Day 2 so you never debug it through the UI.
- [ ] **Strict Pydantic and LLM validation.** Build one `generate_structured()` helper (validate, retry once with the error text, then `LLM_INVALID_OUTPUT`) and route *every* LLM call through it. Never hand-parse JSON anywhere else.
- [ ] **Template rendering with python-pptx.** Render only from the manifest, never by guessing layouts, and keep a 10-slide sample Deck from Day 1 so every later change can be checked with one command.
- Also: run `npm run gen:api` after **every** backend model change, and treat a stale `schema.d.ts` as a bug, not a nuisance.

### 3.2 Day 3 Feature Freeze
**Trigger:** at the 2:30 checkpoint, or at the end of Day 3 at the latest, a full generate does not produce a pptx with a diagram, or it cannot be run from the UI.

**Drop immediately:**
- [ ] **Chat import:**
  - Remove `redact`/`digest` from the pipeline and pass an empty `ChatDigest`.
  - Remove the chat textarea from the Inputs pane.
  - Keep `Source.chat` in the models (the contract stays intact).
  - Accept that the "Pasted LLM chat feeds the problem and decisions slides" Definition-of-Done item is cut.
- [ ] **Manual PATCH editing:**
  - Skip `PATCH /deck` and the `created_by=manual` path.
  - `needs_input` bullets stay highlighted but read-only; fix them through voice edit (`UpdateSlide`).
  - Reclaim the Day 4 chunk at 4:15–4:45.

**Protect at all costs:**
- [ ] Repo ingest, the generated diagram, template-faithful rendering, and voice edit (six ops plus undo).

**If you are still behind after those two cuts** (my suggestion; the architecture doesn't list these):
- [ ] Use the outline preview instead of PNG previews (a sanctioned fallback in the doc).
- [ ] Skip the layout-map override UI, since the heuristic map from upload is enough.

**Reallocate** the reclaimed time to: pipeline reliability, the edit engine tests, and the 3-repo runs.

---

## 4. Demo Recording Checklist (Day 5)

### 4.1 Before recording
- [ ] Prod build served from `localhost:8000`, with `GET /api/health` fully green.
- [ ] A clean DB and `storage/`, or a fresh project; the template is uploaded.
- [ ] Your best test repo is chosen. Pitch notes and the chat text are pasted in a scratch file, ready.
- [ ] Wispr Flow is running and confirmed to type into the command box.
- [ ] Notifications are off and the browser zoom is readable. `.env`, API keys, and personal tabs are hidden.
- [ ] A pre-generated fallback deck for the repo is saved in case a live run fails.

### 4.2 Recording flow (~2 minutes)
- [ ] **0:00–0:15, Hook:** one sentence on the problem (decks take hours, hackathon demos need them fast) and what DeckAgent does.
- [ ] **0:15–0:40, Inputs:** paste the repo URL/path, pitch notes, and LLM chat; pick the template. Click Generate.
- [ ] **0:40–1:00, Live generation:** show SSE progress through the stages.
- [ ] **1:00–1:20, Result:** scroll the deck in your template. Point out the **source badges** and a highlighted `needs_input` bullet. Show the **architecture diagram generated from the actual code**, and that the chat fed the problem and decisions slides.
- [ ] **1:20–1:45, Voice editing:** hold the Wispr key and say "Shorten slide 3 and move demo up." Show the summary line, the new version chip, and then **Undo**.
- [ ] **1:45–2:00, Payoff:** download the `.pptx` and open it in PowerPoint to show the clean, template-faithful result. End with a one-line close.

### 4.3 After recording
- [ ] Watch it once: the audio is clear, nothing sensitive is visible, and the length is within the limit.
- [ ] Upload the video and check the link works while logged out.
- [ ] Add the **usage card screenshot** and the write-up.
- [ ] Confirm each Definition-of-Done item against what the video shows:
  - [ ] Repo plus pitch notes produce a deck in your template.
  - [ ] The diagram comes from the real code and sits in the diagram slide.
  - [ ] Source badges and `needs_input` are visible.
  - [ ] Pasted chat feeds problem and decisions (skip if cut by the freeze).
  - [ ] Voice edits work, with undo.
  - [ ] The download opens cleanly in PowerPoint.
  - [ ] It works on 3 different old repos (mention the results in the write-up).
- [ ] Submit before the deadline with a buffer, and save the confirmation.

---

**Note on stage 2 (`redact`):** the architecture stores only `chat_redacted` and never the raw chat. So the actual `redact()` call lives in `PUT /inputs` on Day 2, and the generate stage just emits its event from the stored text. That satisfies both rules without changing anything in the architecture document.
