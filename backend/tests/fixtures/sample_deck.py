from app.models.deck import Deck, Field, Slide, SlideType, Source
from app.models.facts import ComponentGraph, Edge, Node, NodeKind


def build_sample_deck(template_id: str, project_id: str = "_smoke") -> Deck:
    diagram = ComponentGraph(
        nodes=[
            Node(id="frontend", label="React Frontend", kind=NodeKind.FRONTEND),
            Node(id="backend", label="FastAPI Backend", kind=NodeKind.BACKEND),
            Node(id="db", label="SQLite Database", kind=NodeKind.DB),
            Node(id="ai", label="Gemini LLM", kind=NodeKind.AI),
            Node(id="worker", label="PPTX Renderer", kind=NodeKind.INFRA),
        ],
        edges=[
            Edge(from_="frontend", to="backend", label="REST / SSE"),
            Edge(from_="backend", to="db", label="WAL Queries"),
            Edge(from_="backend", to="ai", label="Structured Facts"),
            Edge(from_="backend", to="worker", label="Render Jobs"),
        ],
    )

    slides = [
        # 1. title
        Slide(
            type=SlideType.TITLE,
            title=Field(
                text="DeckAgent: Autonomous Pitch Deck Generator",
                source=Source.REPO,
                evidence="README.md",
            ),
            subtitle=Field(
                text="Transform Codebases and Chat Digests into Real Presentations",
                source=Source.VOICE,
            ),
            bullets=[],
            image_ref=None,
            notes="Welcome everyone. Today we are demonstrating DeckAgent, an autonomous pitch deck generator.",
        ),
        # 2. problem
        Slide(
            type=SlideType.PROBLEM,
            title=Field(
                text="Manual Deck Creation is Slow and Out of Sync",
                source=Source.CHAT,
            ),
            subtitle=None,
            bullets=[
                Field(
                    text="Hackathon participants spend hours hand-formatting slide templates instead of coding.",
                    source=Source.CHAT,
                ),
                Field(
                    text="Pitch claims frequently drift from actual repository implementation details.",
                    source=Source.USER,
                ),
                Field(
                    text="TODO: Add metric on average deck creation time from user interview transcripts.",
                    source=Source.NEEDS_INPUT,
                ),
            ],
            image_ref=None,
            notes="The problem is acute during hackathons where developers scramble to format slides at the last minute.",
        ),
        # 3. solution
        Slide(
            type=SlideType.SOLUTION,
            title=Field(
                text="Deterministic AI Pipeline with Exact Template Fitting",
                source=Source.CHAT,
            ),
            subtitle=None,
            bullets=[
                Field(
                    text="Parses real code and chat history into schema-validated facts with source provenance.",
                    source=Source.REPO,
                    evidence="backend/app/services/extract.py",
                ),
                Field(
                    text="Renders directly into target PowerPoint templates respecting placeholder size limits.",
                    source=Source.VOICE,
                ),
                Field(
                    text="Iterative voice and text commands allow rapid refinements without breaking styling.",
                    source=Source.USER,
                ),
            ],
            image_ref=None,
            notes="Our solution marries LLM fact extraction with strict deterministic python-pptx template layout.",
        ),
        # 4. features (5 bullets near 100 chars)
        Slide(
            type=SlideType.FEATURES,
            title=Field(
                text="Core Capabilities Built for Hackathon Teams",
                source=Source.REPO,
                evidence="README.md",
            ),
            subtitle=None,
            bullets=[
                Field(
                    text="Deep repository ingestion inspects trees, entrypoints, and route manifests under token caps.",
                    source=Source.REPO,
                    evidence="backend/app/services/ingest.py",
                ),
                Field(
                    text="Automated secret redaction cleans exported team chats before structured LLM fact extraction.",
                    source=Source.REPO,
                    evidence="backend/app/services/chat_digest.py",
                ),
                Field(
                    text="Deterministic Graphviz pipeline generates styled architecture diagrams without messy raw DOT.",
                    source=Source.REPO,
                    evidence="backend/app/services/diagram.py",
                ),
                Field(
                    text="Placeholder measurement engine calculates typography fit limits to prevent text overflow bugs.",
                    source=Source.REPO,
                    evidence="backend/app/services/template_inspector.py",
                ),
                Field(
                    text="Instant versioning and undo stack enables fearless command editing with full SSE replay logs.",
                    source=Source.REPO,
                    evidence="backend/app/core/jobs.py",
                ),
            ],
            image_ref=None,
            notes="These five features provide end-to-end automation from git clone to final downloadable presentation.",
        ),
        # 5. architecture
        Slide(
            type=SlideType.ARCHITECTURE,
            title=Field(
                text="Pipeline Architecture and System Topology",
                source=Source.REPO,
                evidence="backend/app/main.py",
            ),
            subtitle=None,
            bullets=[
                Field(
                    text="FastAPI backend coordinates ingestion, LLM structured calls, and python-pptx rendering.",
                    source=Source.REPO,
                    evidence="backend/app/main.py",
                ),
                Field(
                    text="SQLite database with WAL mode manages atomic version commits and event replay streams.",
                    source=Source.REPO,
                    evidence="backend/app/db.py",
                ),
            ],
            image_ref="diagram",
            notes="Notice the clear separation: LLMs only emit structured data, while rendering is 100% deterministic.",
        ),
        # 6. tech_stack
        Slide(
            type=SlideType.TECH_STACK,
            title=Field(
                text="Modern Lightweight Stack with Zero Drift",
                source=Source.REPO,
                evidence="backend/pyproject.toml",
            ),
            subtitle=None,
            bullets=[
                Field(
                    text="FastAPI, Pydantic v2, SQLModel, and python-pptx on Python 3.11+ backend.",
                    source=Source.REPO,
                    evidence="backend/pyproject.toml",
                ),
                Field(
                    text="Vite, React 19, TailwindCSS, and TanStack Query on modern frontend.",
                    source=Source.REPO,
                    evidence="frontend/package.json",
                ),
                Field(
                    text="TODO: Evaluate lightweight local vector index if repository token count exceeds 12k budget.",
                    source=Source.NEEDS_INPUT,
                ),
            ],
            image_ref=None,
            notes="Every piece of technology was selected to guarantee deterministic rendering and rapid iteration.",
        ),
        # 7. demo
        Slide(
            type=SlideType.DEMO,
            title=Field(
                text="Live Walkthrough: From GitHub URL to 10-Slide Deck",
                source=Source.VOICE,
            ),
            subtitle=None,
            bullets=[
                Field(
                    text="Step 1: Point to GitHub repository and paste developer chat notes.",
                    source=Source.VOICE,
                ),
                Field(
                    text="Step 2: Watch real-time SSE progress events through extraction and render.",
                    source=Source.VOICE,
                ),
                Field(
                    text="Step 3: Download presentation and open immediately in Microsoft PowerPoint.",
                    source=Source.USER,
                ),
            ],
            image_ref=None,
            notes="In this live demonstration, we generate the full deck in under two minutes with complete provenance.",
        ),
        # 8. impact
        Slide(
            type=SlideType.IMPACT,
            title=Field(
                text="Measurable Time Savings for Technical Founders",
                source=Source.VOICE,
            ),
            subtitle=None,
            bullets=[
                Field(
                    text="Reduces pitch preparation overhead from 4 hours down to under 5 minutes.",
                    source=Source.VOICE,
                ),
                Field(
                    text="Eliminates hallucinations through rigorous source attribution and TODO flagging.",
                    source=Source.USER,
                ),
                Field(
                    text="TODO: Gather feedback from pilot users at upcoming university hackathons.",
                    source=Source.NEEDS_INPUT,
                ),
            ],
            image_ref=None,
            notes="The impact is immediate: teams can spend the entire hackathon building instead of formatting slides.",
        ),
        # 9. roadmap
        Slide(
            type=SlideType.ROADMAP,
            title=Field(
                text="Upcoming Milestones and Next Steps",
                source=Source.USER,
            ),
            subtitle=None,
            bullets=[
                Field(
                    text="Phase 1: Real-time headless LibreOffice thumbnail generation for browser previews.",
                    source=Source.REPO,
                    evidence="backend/app/services/preview.py",
                ),
                Field(
                    text="Phase 2: Wispr Flow direct voice dictation into the command input box.",
                    source=Source.VOICE,
                ),
                Field(
                    text="TODO: Support Google Slides export via cloud conversion service.",
                    source=Source.NEEDS_INPUT,
                ),
            ],
            image_ref=None,
            notes="Our roadmap focuses on tightening the voice editing loop and expanding cloud export formats.",
        ),
        # 10. closing
        Slide(
            type=SlideType.CLOSING,
            title=Field(
                text="Thank You: Questions & Next Steps",
                source=Source.VOICE,
            ),
            subtitle=Field(
                text="DeckAgent: Autonomous Pitch Deck Generation for Hackathons",
                source=Source.VOICE,
            ),
            bullets=[],
            image_ref=None,
            notes="Thank you for your time. We welcome any questions and invite you to try DeckAgent today.",
        ),
    ]

    return Deck(
        schema_version=1,
        project_id=project_id,
        version=1,
        template_id=template_id,
        slides=slides,
        diagram=diagram,
    )
