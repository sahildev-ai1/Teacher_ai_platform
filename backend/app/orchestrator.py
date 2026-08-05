"""
Orchestrates the 10-stage pipeline.

Two execution modes, matching how the client described the expected UX
(client Q&A #5: a small number of upfront clarifying questions, then the
system "autonomously orchestrates the remaining stages"):

  A) AUTO-RUN (Stages 1-3): kicked off right after upload as a background
     task. No user input needed once the upfront hints (grade/style/time,
     doc-type) are given, so this runs straight through with progress streamed
     via the PipelineRun row (polled by the SSE endpoint in routers.py).

  B) INTERACTIVE (Stages 4-8): each stage is generated on request, shown to
     the teacher in a chat panel, revised on feedback, and explicitly
     approved before the NEXT stage unlocks (routers.py enforces the gate via
     `STAGE_ORDER` below). This is the "hard single-document, sequential
     checkpoint" design.

  Stages 9-10 (validation + publishing) run synchronously once Stage 8 is
  approved -- fast enough not to need their own background job.
"""
import logging
import traceback

from sqlalchemy.orm import Session

from .db_models import Document, PipelineRun, StageOutput
from .document_intelligence import parse_document
from .stages_extraction import classify_document, extract_knowledge
from . import vector_store

logger = logging.getLogger("teacher_ai.orchestrator")

STAGE_ORDER = [
    "stage4_teaching_plan",
    "stage5_classroom_content",
    "stage6_activities",
    "stage7_assessments",
    "stage8_learning_gaps",
]


def _get_or_create_run(db: Session, document_id: str) -> PipelineRun:
    run = db.query(PipelineRun).filter(PipelineRun.document_id == document_id).first()
    if not run:
        run = PipelineRun(document_id=document_id, current_stage="stage1_document_intelligence", progress=0)
        db.add(run)
        db.commit()
        db.refresh(run)
    return run


def _update_run(db: Session, run: PipelineRun, **kwargs):
    for k, v in kwargs.items():
        setattr(run, k, v)
    db.add(run)
    db.commit()


def run_intelligence_pipeline(db_factory, document_id: str, doc_type_hint: str,
                               grade_hint: str = "", subject_hint: str = ""):
    """Runs Stages 1-3 end to end. Designed to be called as a FastAPI
    BackgroundTasks target, so it opens its OWN db session (`db_factory`) --
    never reuse a request-scoped session inside a background task."""
    db = db_factory()
    try:
        document = db.query(Document).filter(Document.id == document_id).first()
        run = _get_or_create_run(db, document_id)
        _update_run(db, run, state="running", current_stage="preflight", progress=1,
                    message="Checking connections...")

        # Fail fast: if Qdrant is configured but unreachable/misconfigured,
        # find out in seconds, not after burning through several minutes of
        # LLM calls in Stage 2-3 only to fail on indexing at the very end.
        try:
            vector_store.healthcheck()
        except Exception as exc:  # noqa: BLE001
            logger.error("qdrant_preflight_failed document_id=%s error=%s", document_id, exc)
            _update_run(db, run, state="error",
                        message=f"Can't reach Qdrant (check QDRANT_URL/QDRANT_API_KEY): {exc}")
            return

        _update_run(db, run, current_stage="stage1_document_intelligence", progress=5,
                    message="Parsing document...")

        parsed = parse_document(document.raw_text_path, document.file_type)
        document.structure_json = parsed.structure
        db.add(document)
        db.commit()
        _update_run(db, run, progress=20, current_stage="stage2_classification",
                    message="Classifying document...")

        classification = classify_document(parsed, doc_type_hint)
        # user-provided hints (if any) override the model's guess -- the
        # teacher knows the grade/subject better than a heuristic does.
        if grade_hint:
            classification.grade = grade_hint
        if subject_hint:
            classification.subject = subject_hint
        document.classification_json = classification.model_dump()
        db.add(document)
        db.commit()
        _update_run(db, run, progress=35, current_stage="stage3_knowledge_extraction",
                    message="Extracting concepts (batch 0)... this stage is the slowest -- "
                            "safe to close this tab and check back in 20-30 minutes.")

        def _progress_cb(done, total):
            pct = 35 + int(50 * done / max(total, 1))
            _update_run(db, run, progress=pct, message=f"Extracting concepts (batch {done}/{total})...")

        knowledge = extract_knowledge(
            parsed, classification.subject, classification.grade, progress_cb=_progress_cb
        )
        document.knowledge_json = knowledge.model_dump()
        db.add(document)
        db.commit()
        _update_run(db, run, progress=90, message="Indexing document for grounding checks...")

        # Indexing failures are deliberately NON-FATAL: Stage 2-3's expensive,
        # already-committed LLM work must not be thrown away because of a
        # transient Qdrant hiccup (timeout, network blip) at the very last
        # step. The teacher can still proceed to Stage 4 -- Stage 9's
        # grounding check just won't have anything to compare against for
        # this document if this failed.
        try:
            vector_store.ingest_chunks(db, document_id, parsed.chunks)
        except Exception as exc:  # noqa: BLE001
            logger.error("ingest_chunks_failed document_id=%s error=%s", document_id, exc)
            _update_run(db, run, message="Indexing failed (grounding checks will be skipped for "
                                          "this document) -- continuing anyway.")

        document.status = "ready_for_planning"
        db.add(document)
        db.commit()
        _update_run(db, run, progress=100, state="waiting_user",
                    current_stage="stage4_teaching_plan",
                    message="Ready. Waiting for teaching-plan preferences.")
    except Exception as exc:  # noqa: BLE001
        logger.error("pipeline_failed document_id=%s error=%s\n%s", document_id, exc, traceback.format_exc())
        run = _get_or_create_run(db, document_id)
        _update_run(db, run, state="error", message=str(exc))
    finally:
        db.close()


def next_stage_after(stage: str) -> str | None:
    if stage not in STAGE_ORDER:
        return STAGE_ORDER[0]
    idx = STAGE_ORDER.index(stage)
    return STAGE_ORDER[idx + 1] if idx + 1 < len(STAGE_ORDER) else None


def is_stage_unlocked(db: Session, document_id: str, stage: str) -> bool:
    """Stage 4 unlocks once Stages 1-3 are done. Every later stage requires
    the PREVIOUS stage in STAGE_ORDER to be approved."""
    document = db.query(Document).filter(Document.id == document_id).first()
    if stage == "stage4_teaching_plan":
        return document is not None and document.status in ("ready_for_planning", "planning", "generating")
    idx = STAGE_ORDER.index(stage)
    prev_stage = STAGE_ORDER[idx - 1]
    prev = (
        db.query(StageOutput)
        .filter(StageOutput.document_id == document_id, StageOutput.stage == prev_stage)
        .first()
    )
    return prev is not None and prev.status == "approved"
