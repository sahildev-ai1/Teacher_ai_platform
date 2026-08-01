# Teacher Knowledge Package Studio

Converts a raw educational document (PDF / DOCX / PPTX / TXT) into a classroom-ready
**Teacher Knowledge Package (TKP)**: a multi-period teaching plan, per-period classroom
content, activities, assessments, and a learning-gap analysis — grounded in the source
document and reviewed interactively by the teacher before publishing.

Built for a **free-tier deploy** (e.g. Render's free instance, 512MB RAM): no torch, no
separate vector database process, no system-level native dependencies.

---

## Architecture

```
                         ┌─────────────────────────────────────────┐
                         │              React Frontend              │
                         │  Upload → SSE progress → Stage Workspace  │
                         └───────────────────┬───────────────────────┘
                                              │ REST + SSE
                         ┌───────────────────▼───────────────────────┐
                         │              FastAPI Backend               │
                         │                                             │
   AUTO-RUN (background) │  Stage 1  Document Intelligence             │
   ──────────────────────┤  Stage 2  Educational Classification        │
   streamed via SSE       │  Stage 3  Knowledge Extraction (map-reduce) │
                         │                                             │
   INTERACTIVE, gated     │  Stage 4  Teaching Planner      ┐          │
   one-at-a-time, each    │  Stage 5  Classroom Content      │ chat +   │
   requires approval      │  Stage 6  Activities             │ regen +  │
   before the next        │  Stage 7  Assessments            │ approve  │
   unlocks                │  Stage 8  Learning Gap Analysis ┘          │
                         │                                             │
   AUTO-RUN (sync)        │  Stage 9  Validation (schema / hallucination│
                         │            / completeness / consistency)   │
                         │  Stage 10 Publishing (JSON/MD/HTML/PDF/DOCX)│
                         └───────────────────┬───────────────────────┘
                                              │
                    ┌─────────────────────────▼─────────────────────────┐
                    │                  SQLite (ONE file)                  │
                    │  documents · stage_outputs · chat_messages ·        │
                    │  pipeline_runs · embedding_chunks (float32 blobs)   │
                    └───────────────────────────────────────────────────┘
```

**Why one SQLite file instead of Postgres + a vector DB?** This is a single-tenant,
single-document-at-a-time app by design (see "Single-document lock" below), running on
a resource-capped instance. A second persistence system (Chroma, Pinecone, Postgres)
buys nothing here and costs RAM/disk/build-time. Embeddings are stored as raw float32
byte blobs in a `embedding_chunks` table; retrieval is an in-process numpy cosine
similarity search (`app/vector_store.py`) — fast at chapter-sized chunk counts (tens to
low hundreds of rows).

### Why fastembed instead of sentence-transformers?
`sentence-transformers` pulls in `torch`, which alone can exceed a 512MB free-tier RAM
budget. `fastembed` runs the same MiniLM model on ONNX Runtime — no torch, ~90MB on
disk, comfortably under budget alongside FastAPI/uvicorn. See `app/vector_store.py`.

### Why xhtml2pdf instead of WeasyPrint?
WeasyPrint needs system Cairo/Pango/GDK-Pixbuf packages not present on Render's default
Python build image (would require a custom Dockerfile). `xhtml2pdf` is pure Python
(ReportLab-based) and needs nothing extra.

### AI orchestration pattern
Custom, explicit multi-stage pipeline (`app/orchestrator.py`, `app/stages_extraction.py`,
`app/stages_generation.py`) rather than a LangChain agent executor — the workflow is a
known, fixed 10-stage DAG with a human-in-the-loop checkpoint in the middle, which is
more reliably (and more debuggably) expressed as explicit Python functions with a state
machine (`StageOutput.status`: pending → generated → approved) than as an autonomous
agent deciding its own next action. LangChain's text splitter is used for chunking;
LangChain is **not** used as a chat/agent framework here, deliberately.

- **Stage 2 (classification)** is answered from a *compressed global view* of the whole
  document (headings + head/tail + sampled middle chunks) in one call — not via
  similarity retrieval, since "what is this document?" isn't a retrieval query.
- **Stage 3 (knowledge extraction)** is **map-reduce**: a fast model extracts partial
  knowledge per chunk-batch, then the main model merges/deduplicates everything. Scales
  to a full chapter without truncating content.
- Every generation call goes through `llm_client.chat_structured()`, which validates
  the model's JSON against the exact Pydantic schema for that stage and, on a mismatch,
  sends the model its own validation errors and retries (up to 2 extra calls) before
  giving up with a clean `502` — an open ~30B model producing deeply nested JSON *will*
  occasionally drop a field, so this turned a real failure mode into a self-healing one
  (verified in `test_pipeline_e2e.py`, which deliberately breaks the first classification
  response to prove the retry recovers).
- **Stages 4-8** are interactive: generate → teacher reviews in a chat panel → feedback
  triggers a revision (not a restart) → approve unlocks the next stage. This is the
  "hard single-document, sequential checkpoint" design.
- **Stage 9 (validation)** checks: schema adherence, hallucination/grounding (cosine
  similarity of generated *factual* content against the source's embedded chunks —
  mentor moments and activities are excluded from this check by design, since they're
  meant to bring in outside analogies), completeness (every planned concept traces back
  to the knowledge base), and cross-stage period-numbering consistency.

### Single-document lock + delete/reset
`settings.enforce_single_document` (on by default) rejects a new upload with `409` while
any document row exists. The **"Delete & Start New"** button in the header (or
`DELETE /api/documents/{id}`) wipes the uploaded file, all stage outputs, chat history,
embedding vectors, and exported files for that document — after which a new upload is
allowed. This keeps the free-tier instance's memory/disk footprint bounded to one
document's worth of state at a time.

### Bonus features implemented
- **RAG & traceability**: Stage 9's hallucination score is a real embedding-similarity
  check against the source document, not a heuristic; concepts carry `source_ref`.
- **Multi-agent-style separation**: each stage is an independently callable, independently
  testable function/module with a narrow prompt contract — not one mega-prompt.
- **Performance/cost optimization**: map-reduce batching in Stage 3, a cheaper/faster
  model for map steps vs. the main model for synthesis, single in-process vector store.
- **Observability**: structured logging with latency/attempt counts on every LLM call
  (`app/llm_client.py`), plus exponential-backoff retries and one JSON-repair retry.
- **Multilingual-ready**: `Classification.language` is extracted per-document; prompts
  don't hardcode English.
- Optional **Tavily web search** (`TAVILY_API_KEY`) enriches Stages 4-6 with teaching
  strategy / analogy ideas — explicitly labeled as secondary/non-factual in the prompt,
  per the client's grounding rule (secondary sources may shape *pedagogy*, never *facts*).

---

## Setup (local)

### Verifying the pipeline without an Ollama key
`backend/test_pipeline_e2e.py` runs the **entire** pipeline — upload, Stage 1-3
auto-run, all of Stages 4-8 (including a chat-feedback regeneration and a
deliberately-broken model response to exercise the schema-repair retry),
Stage 9 validation, Stage 10 publishing, and every export format — against a
mocked LLM and mocked embeddings, so you can confirm the orchestration logic
end to end before spending a single real API call:
```bash
cd backend
pip install -r requirements.txt
PYTHONPATH=. python3 test_pipeline_e2e.py   # -> "ALL CHECKS PASSED."
```

### Backend
```bash
cd backend
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # fill in OLLAMA_API_KEY (or set OLLAMA_HOST for a local daemon)
uvicorn app.main:app --reload --port 8000
```

### Frontend
```bash
cd frontend
npm install
npm run dev   # http://localhost:5173, expects backend at http://localhost:8000
```

### Deploying to Render (free tier)
- Backend: use `backend/render.yaml`, or a manual Web Service with
  `pip install -r requirements.txt` / `uvicorn app.main:app --host 0.0.0.0 --port $PORT --workers 1`.
  **`--workers 1` is required** — a second worker would load a second copy of the
  embedding model and can blow the RAM budget.
- Set `OLLAMA_API_KEY` (Ollama Cloud) as a secret env var.
- Render's free-tier disk is **ephemeral** — `SQLITE_PATH`/`UPLOAD_DIR`/`EXPORT_DIR`
  reset on redeploy/restart. Fine for a demo/prototype; for persistence beyond that,
  attach a Render persistent disk or point `SQLITE_PATH` at one.
- Frontend: any static host (Render Static Site, Vercel, Netlify) with
  `VITE_API_BASE=https://<your-backend>.onrender.com`.

---

## Repo layout
```
backend/app/
  config.py                 settings (env-driven)
  database.py, db_models.py SQLite via SQLAlchemy
  schemas.py                Pydantic contract for every stage + the final TKP
  document_intelligence.py  Stage 1: parsing (pdf/docx/pptx/txt) + chunking
  llm_client.py              Ollama chat client (retries/logging) + optional Tavily
  vector_store.py           fastembed + in-SQLite cosine-similarity retrieval
  stages_extraction.py      Stage 2 (classification) + Stage 3 (map-reduce extraction)
  stages_generation.py      Stages 4-8 (interactive, feedback-revisable)
  validator.py              Stage 9
  publisher.py              Stage 10: TKP assembly + MD/HTML/PDF/DOCX renderers
  orchestrator.py           background auto-run (1-3) + sequential stage gating (4-8)
  routers.py                all API routes
  main.py                   FastAPI app
frontend/src/
  App.jsx, api.js, components/{UploadPanel,ProgressStream,StageWorkspace,ExportMenu}.jsx
samples/
  sample_tkp_physics.json   STEM example (Grade 9 Physics)
  sample_tkp_history.json   Humanities example (Grade 8 History)
```

## Known limitations / next steps
- SQLite + local disk is single-instance by nature; horizontal scaling would need
  Postgres + object storage (S3) for uploads/exports.
- The DOCX/PDF exporters are a minimal Markdown walker, not a full CSS-to-DOCX engine —
  fine for lesson-plan documents, would need more work for complex tables/equations.
- Stage 1's scanned-PDF detection flags low-text-density PDFs but doesn't yet run an
  OCR/vision pass — `ParsedDocument.needs_advanced_parsing` is a ready hook for one.
