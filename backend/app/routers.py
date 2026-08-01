import asyncio
import json
import os
import shutil

from fastapi import APIRouter, Depends, UploadFile, File, Form, HTTPException, BackgroundTasks
from fastapi.responses import StreamingResponse, Response
from sqlalchemy.orm import Session

from .config import settings
from .database import get_db, SessionLocal
from .db_models import Document, PipelineRun, StageOutput, ChatMessage
from .schemas import Classification, KnowledgeBase, TeachingPlan
from . import orchestrator, stages_generation, validator, publisher, vector_store
from .llm_client import LLMError
from .document_intelligence import infer_file_type

router = APIRouter(prefix="/api")

STAGE_INPUT_KEYS = {
    "stage4_teaching_plan": None,
    "stage5_classroom_content": "stage4_teaching_plan",
    "stage6_activities": "stage4_teaching_plan",
    "stage7_assessments": "stage4_teaching_plan",
    "stage8_learning_gaps": None,
}


def _document_or_404(db: Session, document_id: str) -> Document:
    doc = db.query(Document).filter(Document.id == document_id).first()
    if not doc:
        raise HTTPException(404, "Document not found")
    return doc


# ---------------------------------------------------------------- Upload / lock
@router.get("/documents/active")
def get_active_document(db: Session = Depends(get_db)):
    doc = db.query(Document).first()
    if not doc:
        return {"active": False}
    run = db.query(PipelineRun).filter(PipelineRun.document_id == doc.id).first()
    return {
        "active": True,
        "document_id": doc.id,
        "filename": doc.filename,
        "status": doc.status,
        "pipeline_state": run.state if run else None,
        "current_stage": run.current_stage if run else None,
    }


@router.post("/documents/upload")
async def upload_document(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    doc_type_hint: str = Form("not_sure"),
    grade_hint: str = Form(""),
    subject_hint: str = Form(""),
    teaching_style: str = Form(""),
    time_constraints: str = Form(""),
    db: Session = Depends(get_db),
):
    if settings.enforce_single_document and db.query(Document).first() is not None:
        raise HTTPException(
            409,
            "A document is already active on this instance. Delete it first "
            "(this deploy runs one document at a time to fit the free-tier "
            "resource budget) before uploading a new one.",
        )

    os.makedirs(settings.upload_dir, exist_ok=True)
    file_type = infer_file_type(file.filename)
    document = Document(filename=file.filename, file_type=file_type, doc_type_hint=doc_type_hint,
                         status="uploaded")
    db.add(document)
    db.commit()
    db.refresh(document)

    dest_path = os.path.join(settings.upload_dir, f"{document.id}_{file.filename}")
    with open(dest_path, "wb") as f:
        shutil.copyfileobj(file.file, f)
    document.raw_text_path = dest_path
    db.add(document)
    db.commit()

    # Stash the upfront teaching preferences on the pipeline run's message so
    # Stage 4 can pick them up without a second round-trip.
    run = orchestrator._get_or_create_run(db, document.id)
    orchestrator._update_run(
        db, run, message=json.dumps({"teaching_style": teaching_style, "time_constraints": time_constraints})
    )

    background_tasks.add_task(
        orchestrator.run_intelligence_pipeline, SessionLocal, document.id, doc_type_hint, grade_hint, subject_hint
    )
    return {"document_id": document.id, "status": "processing"}


@router.delete("/documents/{document_id}")
def delete_document(document_id: str, db: Session = Depends(get_db)):
    """The 'delete everything' button: wipes the uploaded file, every stage
    output, chat history, embedding vectors, exported files, and the document
    row itself. Required before a new file can be uploaded (single-document lock)."""
    doc = _document_or_404(db, document_id)

    if doc.raw_text_path and os.path.exists(doc.raw_text_path):
        os.remove(doc.raw_text_path)
    export_path = os.path.join(settings.export_dir, document_id)
    if os.path.isdir(export_path):
        shutil.rmtree(export_path, ignore_errors=True)

    vector_store.delete_document_vectors(db, document_id)
    db.query(ChatMessage).filter(ChatMessage.document_id == document_id).delete()
    db.query(StageOutput).filter(StageOutput.document_id == document_id).delete()
    db.query(PipelineRun).filter(PipelineRun.document_id == document_id).delete()
    db.delete(doc)
    db.commit()
    return {"deleted": True}


# ---------------------------------------------------------------- Progress (SSE)
@router.get("/documents/{document_id}/stream")
async def stream_progress(document_id: str):
    async def event_gen():
        last_sent = None
        for _ in range(600):  # ~10 min safety cap
            db = SessionLocal()
            try:
                run = db.query(PipelineRun).filter(PipelineRun.document_id == document_id).first()
            finally:
                db.close()
            if not run:
                yield f"data: {json.dumps({'state': 'unknown'})}\n\n"
                break
            payload = {
                "state": run.state, "progress": run.progress,
                "current_stage": run.current_stage, "message": run.message,
            }
            snapshot = json.dumps(payload)
            if snapshot != last_sent:
                yield f"data: {snapshot}\n\n"
                last_sent = snapshot
            if run.state in ("waiting_user", "done", "error"):
                break
            await asyncio.sleep(0.8)

    return StreamingResponse(event_gen(), media_type="text/event-stream")


# ---------------------------------------------------------------- Document detail
@router.get("/documents/{document_id}")
def get_document(document_id: str, db: Session = Depends(get_db)):
    doc = _document_or_404(db, document_id)
    stages = {s.stage: {"status": s.status, "content": s.content_json, "version": s.version}
              for s in doc.stages}
    return {
        "id": doc.id, "filename": doc.filename, "status": doc.status,
        "structure": doc.structure_json, "classification": doc.classification_json,
        "knowledge_base": doc.knowledge_json, "stages": stages,
    }


# ---------------------------------------------------------------- Stage generate / approve
GENERATORS = {
    "stage4_teaching_plan": stages_generation.generate_teaching_plan,
    "stage5_classroom_content": stages_generation.generate_period_content,
    "stage6_activities": stages_generation.generate_activities,
    "stage7_assessments": stages_generation.generate_assessments,
    "stage8_learning_gaps": stages_generation.generate_learning_gaps,
}


def _run_stage_generation(db: Session, doc: Document, stage: str, feedback: str | None,
                           teaching_style: str = "", time_constraints: str = ""):
    classification = Classification.model_validate(doc.classification_json)
    knowledge_base = KnowledgeBase.model_validate(doc.knowledge_json)

    existing_row = db.query(StageOutput).filter(
        StageOutput.document_id == doc.id, StageOutput.stage == stage
    ).first()
    existing = existing_row.content_json if existing_row else None

    if stage == "stage4_teaching_plan":
        prefs = {}
        run = db.query(PipelineRun).filter(PipelineRun.document_id == doc.id).first()
        if run and run.message:
            try:
                prefs = json.loads(run.message)
            except (json.JSONDecodeError, TypeError):
                prefs = {}
        result = stages_generation.generate_teaching_plan(
            knowledge_base, classification,
            time_constraints=time_constraints or prefs.get("time_constraints"),
            teaching_style=teaching_style or prefs.get("teaching_style"),
            feedback=feedback, existing=existing,
        )
        content_json = result.model_dump()
    else:
        plan_row = db.query(StageOutput).filter(
            StageOutput.document_id == doc.id, StageOutput.stage == "stage4_teaching_plan"
        ).first()
        if not plan_row or not plan_row.content_json:
            raise HTTPException(409, "Approve Stage 4 (Teaching Plan) first.")
        plan = TeachingPlan.model_validate(plan_row.content_json)

        if stage == "stage8_learning_gaps":
            result = stages_generation.generate_learning_gaps(
                knowledge_base, classification, feedback=feedback, existing=existing
            )
            content_json = {"gaps": [g.model_dump() for g in result]}
        else:
            fn = GENERATORS[stage]
            existing_wrapped = existing
            result = fn(plan, knowledge_base, classification, feedback=feedback, existing=existing_wrapped)
            key = {
                "stage5_classroom_content": "periods",
                "stage6_activities": "activities",
                "stage7_assessments": "assessments",
            }[stage]
            content_json = {key: [r.model_dump() for r in result]}

    if existing_row:
        existing_row.content_json = content_json
        existing_row.status = "generated"
        existing_row.version += 1
        db.add(existing_row)
    else:
        db.add(StageOutput(document_id=doc.id, stage=stage, content_json=content_json, status="generated"))
    db.commit()
    return content_json


@router.post("/documents/{document_id}/stages/{stage}/generate")
def generate_stage(document_id: str, stage: str, body: dict, db: Session = Depends(get_db)):
    doc = _document_or_404(db, document_id)
    if stage not in GENERATORS:
        raise HTTPException(404, f"Unknown stage {stage}")
    if not orchestrator.is_stage_unlocked(db, document_id, stage):
        raise HTTPException(409, f"{stage} is locked -- approve the previous stage first.")
    try:
        content = _run_stage_generation(
            db, doc, stage,
            feedback=body.get("feedback"),
            teaching_style=body.get("teaching_style", ""),
            time_constraints=body.get("time_constraints", ""),
        )
    except LLMError as exc:
        raise HTTPException(502, f"AI generation failed for {stage}: {exc}")
    return {"stage": stage, "content": content}


@router.post("/documents/{document_id}/stages/{stage}/approve")
def approve_stage(document_id: str, stage: str, db: Session = Depends(get_db)):
    row = db.query(StageOutput).filter(
        StageOutput.document_id == document_id, StageOutput.stage == stage
    ).first()
    if not row or row.status not in ("generated", "approved"):
        raise HTTPException(409, "Generate this stage at least once before approving.")
    row.status = "approved"
    db.add(row)
    db.commit()
    nxt = orchestrator.next_stage_after(stage)
    return {"approved": stage, "next_stage": nxt}


# ---------------------------------------------------------------- Chat (per stage)
@router.get("/documents/{document_id}/chat/{stage}")
def get_chat(document_id: str, stage: str, db: Session = Depends(get_db)):
    msgs = db.query(ChatMessage).filter(
        ChatMessage.document_id == document_id, ChatMessage.stage == stage
    ).order_by(ChatMessage.created_at).all()
    return [{"role": m.role, "content": m.content, "created_at": m.created_at.isoformat()} for m in msgs]


@router.post("/documents/{document_id}/chat/{stage}")
def send_chat(document_id: str, stage: str, body: dict, db: Session = Depends(get_db)):
    """A teacher message here both logs to chat history AND triggers a
    regeneration of the stage using that message as feedback -- this is the
    'chatbot with context + history that revises content' behaviour."""
    doc = _document_or_404(db, document_id)
    message = body.get("message", "").strip()
    if not message:
        raise HTTPException(400, "message is required")
    if not orchestrator.is_stage_unlocked(db, document_id, stage):
        raise HTTPException(409, f"{stage} is locked -- approve the previous stage first.")

    db.add(ChatMessage(document_id=document_id, stage=stage, role="user", content=message))
    db.commit()

    try:
        content = _run_stage_generation(db, doc, stage, feedback=message)
    except LLMError as exc:
        db.add(ChatMessage(
            document_id=document_id, stage=stage, role="assistant",
            content=f"I couldn't apply that revision (AI generation error): {exc}",
        ))
        db.commit()
        raise HTTPException(502, f"AI generation failed for {stage}: {exc}")

    db.add(ChatMessage(
        document_id=document_id, stage=stage, role="assistant",
        content="Updated based on your feedback. Review the panel and approve when you're happy with it.",
    ))
    db.commit()
    return {"stage": stage, "content": content}


# ---------------------------------------------------------------- Publish (Stage 9 + 10)
@router.post("/documents/{document_id}/publish")
def publish(document_id: str, db: Session = Depends(get_db)):
    doc = _document_or_404(db, document_id)
    stage_rows = {s.stage: s for s in doc.stages}
    for stage in orchestrator.STAGE_ORDER:
        row = stage_rows.get(stage)
        if not row or row.status != "approved":
            raise HTTPException(409, f"Approve {stage} before publishing.")

    classification = Classification.model_validate(doc.classification_json)
    knowledge_base = KnowledgeBase.model_validate(doc.knowledge_json)
    plan = TeachingPlan.model_validate(stage_rows["stage4_teaching_plan"].content_json)

    from .schemas import PeriodContent, Activity, Assessment, LearningGap
    content = [PeriodContent.model_validate(p) for p in stage_rows["stage5_classroom_content"].content_json["periods"]]
    activities = [Activity.model_validate(a) for a in stage_rows["stage6_activities"].content_json["activities"]]
    assessments = [Assessment.model_validate(a) for a in stage_rows["stage7_assessments"].content_json["assessments"]]
    gaps = [LearningGap.model_validate(g) for g in stage_rows["stage8_learning_gaps"].content_json["gaps"]]

    report = validator.validate_package(db, document_id, classification, knowledge_base, plan, content, activities, assessments, gaps)
    package = publisher.build_package(document_id, classification, knowledge_base, plan, content, activities, assessments, gaps, report)

    os.makedirs(os.path.join(settings.export_dir, document_id), exist_ok=True)
    out_path = os.path.join(settings.export_dir, document_id, "TeacherKnowledgePackage.json")
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(package.model_dump_json(indent=2))

    doc.status = "published"
    db.add(doc)
    db.commit()
    return json.loads(package.model_dump_json())


@router.get("/documents/{document_id}/export")
def export_package(document_id: str, format: str = "json", db: Session = Depends(get_db)):
    path = os.path.join(settings.export_dir, document_id, "TeacherKnowledgePackage.json")
    if not os.path.exists(path):
        raise HTTPException(404, "Publish this document first (Stage 10).")
    with open(path, "r", encoding="utf-8") as f:
        from .schemas import TeacherKnowledgePackage
        package = TeacherKnowledgePackage.model_validate_json(f.read())

    if format == "json":
        return Response(content=package.model_dump_json(indent=2), media_type="application/json")

    md_text = publisher.render_markdown(package)
    if format == "md":
        return Response(content=md_text, media_type="text/markdown",
                         headers={"Content-Disposition": "attachment; filename=teacher_guide.md"})
    html_text = publisher.render_html(md_text)
    if format == "html":
        return Response(content=html_text, media_type="text/html")
    if format == "pdf":
        pdf_bytes = publisher.render_pdf(html_text)
        return Response(content=pdf_bytes, media_type="application/pdf",
                         headers={"Content-Disposition": "attachment; filename=teacher_guide.pdf"})
    if format == "docx":
        docx_bytes = publisher.render_docx(md_text)
        return Response(content=docx_bytes,
                         media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                         headers={"Content-Disposition": "attachment; filename=teacher_guide.docx"})
    raise HTTPException(400, "format must be one of: json, md, html, pdf, docx")
