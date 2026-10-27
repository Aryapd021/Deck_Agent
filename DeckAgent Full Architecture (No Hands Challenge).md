# DeckAgent: Full Architecture

Working name. A voice-editable agent that turns a hackathon project (GitHub repo + dictated pitch + LLM chat history) into a template-faithful PowerPoint deck with a generated architecture diagram. Built solo for the WeMakeDevs x Wispr Flow challenge, Builders track.

## 1. Design principles (these prevent collisions)

1. **One source of truth: the Deck JSON.** The .pptx, the PNG previews and the UI are all *derived* from it. Nothing ever edits a .pptx directly.
2. **Contract first.** Pydantic models in the backend define every shape. The frontend TypeScript types are *generated* from the backend's OpenAPI. Types cannot drift.
3. **One origin.** In production FastAPI serves the built frontend, so there is no CORS. In dev, Vite proxies `/api` to FastAPI. Same URLs in both.
4. **Immutable versions.** Every change creates a new `deck_version`. Files are named by version, so nothing is overwritten and nothing races.
5. **One active job per project.** A per-project lock rejects overlapping generate/edit calls with a clear 409.
6. **LLMs only produce data, never files.** They return schema-validated JSON. Deterministic code does layout, diagrams and rendering.
7. **No audio code.** Wispr Flow types into any text field at OS level, so voice editing is just a text command box.

## 2. Stack (final, no alternatives)

| Layer | Choice | Why it won't collide |
| --- | --- | --- |
| Backend | Python 3.11, FastAPI, Uvicorn (single worker) | One process means in-process locks are enough |
| Validation / contract | Pydantic v2 | Generates JSON Schema and OpenAPI |
| Frontend | Vite + React + TypeScript + Tailwind | Static build, served by FastAPI |
| Server state | TanStack Query | Server is the truth, UI never owns deck data |
| Types | `openapi-typescript` | Generated from `/openapi.json` |
| Live progress | Server-Sent Events (SSE) | One-way stream, simpler than WebSockets |
| DB | SQLite via SQLModel | Zero setup, one file |
| Files | Local `storage/` directory | Versioned paths |
| Jobs | `asyncio` tasks plus `asyncio.to_thread` for blocking work | No Redis, no Celery |
| LLM | Provider interface, one provider active (Gemini or Claude) | Chosen by `.env` |
| Repo access | `git clone --depth 1` or local path | Works for old and private repos |
| PPTX | `python-pptx` | Fills real template layouts |
| Diagram | Graphviz (`dot`) to SVG and PNG | Layout is automatic and deterministic |
| Preview | LibreOffice headless to PDF, PyMuPDF to PNG | Optional; falls back to outline view |
| Config | `pydantic-settings` reading one `.env` | Single config source |

External installs needed: Python 3.11+, Node 20+, Git, Graphviz, and (optional) LibreOffice.

## 3. System overview

```
┌──────────────────────── Browser (React) ────────────────────────┐
│  Inputs pane      │  Deck pane (preview/outline)  │ Command pane │
│  repo URL/path    │  slide thumbnails + diagram   │ voice/text   │
│  pitch notes      │  per-field source badges      │ edit box     │
│  chat import      │  version history + undo       │ job progress │
│  template picker  │  download .pptx               │              │
└───────┬───────────┴───────────────┬───────────────┴──────┬───────┘
        │ REST (/api)               │ SSE (/api/jobs/…)    │
┌───────▼───────────────────────────▼──────────────────────▼───────┐
│                         FastAPI (single origin)                  │
│  routers: templates · projects · jobs · deck · artifacts         │
│  job runner (per-project lock)  ·  SSE event bus                 │
├───────────────────────────────────────────────────────────────────┤
│ services                                                          │
│  ingest → chat_digest → extract → diagram → render → preview      │
│                  edit (command → typed ops → apply → render)      │
│  llm (provider interface)   template_inspector                    │
├───────────────────────────────────────────────────────────────────┤
│  SQLite (projects, versions, jobs, events)  ·  storage/ files     │
└───────────────────────────────────────────────────────────────────┘
```

## 4. Repository layout

```
deckagent/
  .env.example
  README.md
  backend/
    pyproject.toml
    app/
      main.py                # app factory, mounts /api, serves frontend/dist
      config.py              # Settings (pydantic-settings)
      db.py                  # engine, session, create_all
      models/
        deck.py              # Deck, Slide, Field, Source  (THE CONTRACT)
        facts.py             # ProjectFacts, ComponentGraph, ChatDigest
        edit_ops.py          # typed edit operations
        api.py               # request/response models, JobEvent
        tables.py            # SQLModel tables
      api/
        templates.py  projects.py  jobs.py  deck.py  artifacts.py
      core/
        jobs.py              # runner, locks, event bus
        errors.py            # error codes
        storage.py           # path helpers (versioned)
      services/
        llm/  base.py  gemini.py  anthropic.py
        ingest.py  chat_digest.py  extract.py
        diagram.py  render.py  preview.py
        edit.py  template_inspector.py  validate.py
      prompts/               # .md prompt files, loaded by name
    tests/
  frontend/
    package.json  vite.config.ts   # proxy /api to :8000
    src/
      api/schema.d.ts        # GENERATED, never hand-edited
      api/client.ts          # typed fetch + SSE hook
      components/ ...
      App.tsx
  templates/                 # uploaded .pptx + manifests
  storage/                   # projects/<id>/v<N>/{deck.json,deck.pptx,diagram.png,slides/*.png}
```

## 5. The contract: Deck JSON

Every text the agent writes carries provenance. This is the "no invented facts" feature.

```python
class Source(str, Enum):
    repo = "repo"; voice = "voice"; chat = "chat"; user = "user"; needs_input = "needs_input"

class Field(BaseModel):
    text: str
    source: Source
    evidence: str | None = None     # e.g. "README.md" or "src/api/main.py"

class SlideType(str, Enum):
    title; problem; solution; features; architecture; tech_stack; demo; impact; roadmap; closing

class Slide(BaseModel):
    id: str                          # stable uuid, survives reordering
    type: SlideType
    title: Field
    subtitle: Field | None = None
    bullets: list[Field] = []        # max 5 per slide, enforced
    image_ref: str | None = None     # "diagram" or a screenshot slot id
    notes: str = ""                  # speaker notes

class Deck(BaseModel):
    schema_version: int = 1
    project_id: str
    version: int                     # increments on every change
    template_id: str
    slides: list[Slide]
    diagram: ComponentGraph | None
```

**Source priority when inputs disagree:** `user` > `repo` > `voice` > `chat`. If a claim appears only in chat or voice and the repo contradicts it, the repo wins and the field is flagged `needs_input`. Chat content may supply *motivation and decisions*, never feature claims.

**Fit limits** (enforced by `validate.py`): title ≤ 70 chars, bullet ≤ 100 chars, ≤ 5 bullets, notes ≤ 600 chars. If the LLM exceeds them, one automatic "shorten" call runs. If it still fails, the text is hard-truncated at a word boundary and flagged.

### Intermediate shapes

```python
class ProjectFacts(BaseModel):          # extracted once per generate
    name: str; one_liner: Field
    problem: list[Field]; solution: list[Field]
    features: list[Field]; stack: list[Field]
    components: ComponentGraph
    demo_steps: list[Field]; impact: list[Field]; roadmap: list[Field]

class ComponentGraph(BaseModel):
    nodes: list[Node]                   # id, label, kind(frontend|backend|db|ai|external|infra), group?
    edges: list[Edge]                   # from, to, label?
    # constraint: 4 to 8 nodes, every edge references existing node ids

class ChatDigest(BaseModel):
    problem_framing: list[str]; decisions: list[str]
    rejected_options: list[str]; insights: list[str]
```

## 6. Template handling

One-time **template inspection** runs when a template is uploaded:

1. Open with `python-pptx`, list every slide layout: name, index, and its placeholders (`idx`, `type`, name, position and size).
2. Build a **manifest**: `layout_map` from each `SlideType` to a layout index, chosen by name heuristics ("Title Slide", "Title and Content", "Picture with Caption", and so on) and placeholder types.
3. Validate: each used slide type needs a title placeholder, and `architecture` and `demo` need a picture or content area. Anything missing falls back to "Title and Content" and a warning is stored.
4. Store the manifest as `templates/<id>.manifest.json`. The user can edit the mapping in the UI (a dropdown per slide type).

Rendering never guesses: it reads the manifest. Placeholder size feeds the fit limits above, so text cannot overflow.

## 7. Pipeline: generate

```
POST /api/projects/{id}/generate  →  202 {job_id}
```

| # | Stage (SSE `stage`) | Input | Output | Blocking? |
| --- | --- | --- | --- | --- |
| 1 | `ingest` | repo URL/path | `RepoDigest`: tree, languages, manifests, README excerpt, entrypoints, frameworks (token-budgeted, deterministic) | thread |
| 2 | `redact` | chat text | secrets and emails stripped by regex | fast |
| 3 | `digest` | redacted chat | `ChatDigest` (LLM) | network |
| 4 | `facts` | RepoDigest + pitch notes + ChatDigest + criteria | `ProjectFacts` (LLM, schema-validated, one retry) | network |
| 5 | `deck` | ProjectFacts + manifest capabilities | `Deck` v1 (LLM), then `validate.py` | network |
| 6 | `diagram` | `ComponentGraph` | `diagram.svg`, `diagram.png` themed with the template palette | thread |
| 7 | `render` | Deck + template + diagram | `deck.pptx` | thread |
| 8 | `preview` | `deck.pptx` | `slides/N.png` (or skipped) | thread |
| 9 | `done` |  | version committed, event emitted |  |

The version is committed to the DB only after stage 7 succeeds. A failed job leaves the previous version untouched.

**Token budgeting for ingest:** priority order is README, dependency manifests (`package.json`, `requirements.txt`, `pyproject.toml`), top-level tree, entrypoints (`main.*`, `app.*`, `index.*`), then route/model files. Cap at about 12k tokens total. Skip `node_modules`, lockfiles, binaries, build output.

**Diagram rule:** the LLM never writes Graphviz or Mermaid. It returns a `ComponentGraph`, and `diagram.py` converts it to DOT with fixed styling (node shape by `kind`, colors from the template theme, left-to-right layout). Bad LLM output cannot break the drawing.

## 8. Pipeline: voice/text edit

Wispr Flow dictates into the command box. The backend only sees text.

```
POST /api/projects/{id}/edit  {command, base_version, target_slide_id?}  →  202 {job_id}
```

1. **Version check:** if `base_version` ≠ current, return 409 `STALE_VERSION`.
2. **Interpret (LLM):** input is the command, the current Deck JSON (compact), and the selected slide if any. Output is a list of **typed ops**, never free text:

```python
class UpdateSlide:  slide_id, fields: partial Slide content     # shorten, expand, reword
class MoveSlide:    slide_id, to_index
class DeleteSlide:  slide_id
class AddSlide:     after_slide_id, type, content
class SetDiagramFocus: node_ids: list[str]                      # simplify or zoom the diagram
class Regenerate:   slide_id                                    # redo from facts
```

3. **Validate:** every `slide_id` must exist, indexes must be in range, content must pass fit limits. Any failure rejects the whole batch with an error message. Ops apply **atomically**: all or nothing.
4. **Apply** deterministically to a copy of the Deck, then increment `version`.
5. **Re-render** only what changed (diagram only if `SetDiagramFocus`), emit a one-line summary such as "Slide 3: 5 bullets to 3; moved Demo to position 2".
6. **Undo** creates a *new* version copying an older one. History is never rewritten.

Out-of-scope commands return a polite "I can't do that yet" and change nothing.

## 9. API surface

| Method | Path | Purpose |
| --- | --- | --- |
| GET | `/api/health` | Checks dot, git, soffice availability |
| POST | `/api/templates` | Upload .pptx, returns manifest and warnings |
| GET | `/api/templates` | List templates |
| PUT | `/api/templates/{id}/layout-map` | Override the mapping |
| POST | `/api/projects` | Create: name, `repo_url` or `repo_path`, `template_id` |
| PUT | `/api/projects/{id}/inputs` | Pitch notes, chat text (paste or file), criteria |
| POST | `/api/projects/{id}/generate` | Start generate job |
| POST | `/api/projects/{id}/edit` | Start edit job |
| GET | `/api/jobs/{job_id}/events` | SSE stream |
| GET | `/api/projects/{id}/deck` | Latest Deck + version |
| GET | `/api/projects/{id}/versions` | History list |
| POST | `/api/projects/{id}/undo` | New version from the previous one |
| PATCH | `/api/projects/{id}/deck` | Manual field edit (requires `base_version`) |
| GET | `/api/projects/{id}/v/{n}/slides/{k}.png` | Preview image |
| GET | `/api/projects/{id}/v/{n}/deck.pptx` | Download |

**Error envelope (all endpoints):** `{ "code": "STALE_VERSION", "message": "...", "detail": {...} }`. Codes: `JOB_IN_PROGRESS`, `STALE_VERSION`, `LLM_INVALID_OUTPUT`, `REPO_UNREACHABLE`, `TEMPLATE_INVALID`, `TOOL_MISSING`, `NOT_FOUND`.

**SSE event:** `{ seq, stage, status: started|progress|done|error, message, version? }`. Events are saved to `job_events`, so a reconnecting client sends `Last-Event-ID` and replays missed events. A refresh mid-job loses nothing.

## 10. Database tables

| Table | Key columns |
| --- | --- |
| `template` | id, name, path, manifest\_json |
| `project` | id, name, repo\_ref, template\_id, created\_at |
| `project_input` | project\_id (unique), pitch\_notes, chat\_redacted, criteria, updated\_at |
| `deck_version` | project\_id, version, deck\_json, created\_by (`generate`, `edit`, `undo`, `manual`), summary |
| `job` | id, project\_id, type, status, stage, error\_code, started\_at, ended\_at |
| `job_event` | job\_id, seq, stage, status, message, ts |
| `artifact` | project\_id, version, kind (`pptx`, `diagram`, `slide_png`), path |

Unique constraint on `(project_id, version)` makes a double-commit impossible.

## 11. Frontend behavior

- **Three panes:** Inputs (left), Deck (center), Command and progress (right).
- **State:** TanStack Query holds the deck. The UI never mutates it locally. It sends a request, listens to SSE, then refetches `/deck`. No optimistic deck updates, so no divergence.
- **Command box:** large textarea with a "Hold your Wispr key and speak" hint, Enter to send, disabled while a job runs.
- **Source badges:** each bullet shows a small badge (repo, voice, chat, you). `needs_input` items are highlighted with an inline edit field that sends a `PATCH`.
- **Version bar:** v1, v2, v3 chips, an Undo button, and a Download button for the current version.
- **Preview:** PNG slides when LibreOffice is available, otherwise an outline view of the Deck JSON. Both read the same data.
- **Health banner:** if `/api/health` reports a missing tool, a clear message shows instead of a broken pipeline.

## 12. Collision matrix

| Risk | Mitigation |
| --- | --- |
| CORS / port clashes | Vite proxy in dev, FastAPI serves `dist/` in prod; frontend only calls relative `/api` |
| Frontend and backend types drift | `npm run gen:api` regenerates `schema.d.ts` from OpenAPI; CI-style check script fails if it's stale |
| Overlapping generate and edit | Per-project `asyncio.Lock`; second call gets 409 `JOB_IN_PROGRESS` |
| Edit applied to outdated deck | `base_version` required; mismatch returns 409 `STALE_VERSION` |
| Partial writes on failure | Write to a temp dir, then atomic rename to `v<N>`; DB commit last |
| Blocking the event loop | python-pptx, Graphviz, git and PyMuPDF run in `asyncio.to_thread` |
| LLM returns invalid JSON | Schema validation, one retry with the error message, then `LLM_INVALID_OUTPUT` |
| LLM invents facts | Provenance on every field; unsupported claims become `needs_input`; repo outranks chat and voice |
| Text overflows placeholders | Char limits derived from placeholder size; auto-shorten once, then truncate and flag |
| Template layout missing | Manifest fallback plus warning at upload time, not at render time |
| Diagram gets messy | 4 to 8 nodes, fixed DOT styling, `SetDiagramFocus` to simplify |
| SSE disconnect | Events persisted and replayed via `Last-Event-ID` |
| File overwrites | Paths include project id and version; nothing is overwritten |
| Windows path issues | `pathlib` everywhere, no shell string paths, subprocess args as lists |
| API keys leaking | Keys only in server `.env`, never sent to the browser; `.env` in `.gitignore` |
| Secrets in imported chats | Regex redaction before any LLM call; raw chat text is not stored |
| Missing system tools | `/api/health` checks `dot`, `git`, `soffice` at startup; preview degrades gracefully |
| Two uvicorn workers | Run with `--workers 1`; documented in README (locks are in-process) |

## 13. Configuration (`.env`)

```
LLM_PROVIDER=gemini            # or anthropic
GEMINI_API_KEY=
ANTHROPIC_API_KEY=
GITHUB_TOKEN=                  # optional, for private repos
STORAGE_DIR=./storage
PREVIEW_MODE=libreoffice       # or outline
MAX_REPO_TOKENS=12000
MAX_SLIDES=10
```

## 14. Build order (maps to your 5 days at 5 hours each)

| Day | Build | Exit test |
| --- | --- | --- |
| 1 | Repo skeleton, `.env`, Deck/Facts/Ops models, DB, template inspector and manifest, `render.py` from a hardcoded Deck, FastAPI health + template upload, Vite proxy, `gen:api` | Hardcoded Deck renders into your template and opens in PowerPoint; frontend calls `/api/health` with generated types |
| 2 | `ingest`, LLM provider, `extract` (facts then deck), `chat_digest` and redaction, jobs and SSE | One repo plus pitch plus chat produces a valid Deck with provenance |
| 3 | `diagram.py` (Graphviz), image insertion into the template, `preview.py`, frontend three-pane UI | Full generate from the UI shows a deck with diagram |
| 4 | `edit` engine (ops, validate, apply), undo, version history, `needs_input` UI, fit-limit auto-shorten | "Shorten slide 3 and move demo up" works and undoes cleanly |
| 5 | Run on 3 test repos, fix quality, record demo, write-up, usage card, submit | Submitted before 31 Oct, 11:59 pm IST. **No new features on Day 5.** |

**Feature-freeze rule:** if Day 3 ends without a working end-to-end generate, cut chat import and manual PATCH editing, and protect repo, diagram, template and voice edit.

## 15. Definition of done

- [ ] Repo URL or local path plus pitch notes produce a deck in your template
- [ ] Architecture diagram is generated from the actual code and sits in the diagram slide
- [ ] Every bullet has a source badge; unsupported claims show as `needs_input`
- [ ] Pasted LLM chat feeds the problem and decisions slides
- [ ] Voice command edits work for the six op types, with undo
- [ ] Download opens cleanly in PowerPoint
- [ ] Works on 3 different old repos
- [ ] 2-minute demo video, usage card screenshot and write-up ready
