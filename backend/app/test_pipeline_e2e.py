"""
Full pipeline smoke test using a mocked LLM (`llm_client.chat`) and a fake
in-memory stand-in for the Qdrant vector store, since neither Ollama Cloud nor
Qdrant Cloud are reachable from this sandbox's network. Everything else --
FastAPI routing, SQLAlchemy models, the stage state machine, schema
validation + repair retries, the validator, and all five export formats --
runs for real.

Run: PYTHONPATH=. venv/bin/python3 test_pipeline_e2e.py
"""
import json
import os
import shutil
import tempfile

TMP = tempfile.mkdtemp()
os.environ["SQLITE_PATH"] = os.path.join(TMP, "tkp.db")
os.environ["UPLOAD_DIR"] = os.path.join(TMP, "uploads")
os.environ["EXPORT_DIR"] = os.path.join(TMP, "exports")

from app import llm_client, vector_store  # noqa: E402

# ---------------------------------------------------------------- Mock LLM
CALL_COUNTS = {}


def fake_chat(prompt, system=None, model=None, json_mode=False, max_retries=3, timeout=120.0):
    def hit(name):
        CALL_COUNTS[name] = CALL_COUNTS.get(name, 0) + 1
        return CALL_COUNTS[name]

    if "You are classifying an educational document" in prompt or '"title": "Classification"' in prompt:
        n = hit("classification")
        if n == 1:
            # Deliberately missing the required "language" field, to exercise
            # chat_structured's schema-repair retry loop.
            return json.dumps({
                "subject": "Biology", "grade": "7", "difficulty": "Beginner",
                "topic": "Photosynthesis", "chapter": "Chapter 5", "category": "STEM",
                "doc_type": "mostly_text",
            })
        return json.dumps({
            "subject": "Biology", "grade": "7", "difficulty": "Beginner", "topic": "Photosynthesis",
            "chapter": "Chapter 5", "category": "STEM", "language": "English", "doc_type": "mostly_text",
        })

    if ("You are extracting teachable knowledge from a" in prompt
            or "You are merging several partial knowledge extractions" in prompt
            or '"title": "KnowledgeBase"' in prompt):
        return json.dumps({
            "learning_objectives": ["Explain how plants convert light into chemical energy"],
            "prerequisites": ["Basic cell structure"],
            "concepts": [{"name": "Chlorophyll", "explanation": "Pigment in chloroplasts that absorbs light energy."}],
            "definitions": [{"name": "Photosynthesis", "explanation": "Process converting light energy into glucose and oxygen."}],
            "formulae": ["6CO2 + 6H2O -> C6H12O6 + 6O2"],
            "keywords": ["chlorophyll", "glucose", "chloroplast"],
            "examples": ["Leaves turning toward sunlight"],
            "applications": ["Crop yield optimization in agriculture"],
            "common_misconceptions": [{"misconception": "Plants get their food from soil.",
                                        "correction": "Plants make their own food via photosynthesis; soil provides water/minerals, not food."}],
        })

    if "Design a multi-period teaching plan" in prompt or '"title": "TeachingPlan"' in prompt:
        return json.dumps({
            "total_periods": 2,
            "rationale": "Two periods: one for the concept, one for the chemical process and applications.",
            "periods": [
                {"period_number": 1, "title": "What is Photosynthesis?", "duration_minutes": 40,
                 "objectives": ["Explain the role of chlorophyll"], "concepts_covered": ["Chlorophyll"],
                 "pacing_notes": "Use a live plant for observation."},
                {"period_number": 2, "title": "The Photosynthesis Equation", "duration_minutes": 40,
                 "objectives": ["Describe inputs/outputs of photosynthesis"], "concepts_covered": ["Photosynthesis"],
                 "pacing_notes": "Walk through the balanced equation."},
            ],
        })

    if "Generate full classroom content for ONE period" in prompt or '"title": "PeriodContent"' in prompt:
        return json.dumps({
            "period_number": 1, "entry_ticket": "Why do leaves look green?",
            "teacher_script": "Start by asking why leaves are green, then introduce chlorophyll...",
            "blackboard_notes": "Chlorophyll absorbs red/blue light, reflects green.",
            "classroom_activity_summary": "Leaf pigment observation under a lamp.",
            "checkpoint_questions": ["What does chlorophyll do?", "Where in the cell is chlorophyll found?"],
            "exit_ticket": "In one sentence, what is chlorophyll's job?",
            "homework": "Sketch a labeled leaf cross-section.",
            "mentor_moment": "Nearly all life on Earth ultimately depends on photosynthesis for energy.",
        })

    if "Design ONE classroom activity" in prompt or '"title": "Activity"' in prompt:
        return json.dumps({
            "period_number": 1, "title": "Leaf Pigment Chromatography", "type": "experiment", "duration_minutes": 20,
            "materials": ["Spinach leaves", "Filter paper strips", "Rubbing alcohol", "Jars"],
            "instructions": ["Crush leaves into alcohol.", "Dip filter paper strip into the solution.", "Let pigments separate and observe the bands."],
            "success_criteria": ["Students correctly identify at least 2 pigment bands."],
        })

    if "Create an assessment for period" in prompt or '"title": "Assessment"' in prompt:
        return json.dumps({
            "period_number": 1,
            "mcqs": [{"question": "Which pigment gives leaves their green color?",
                       "options": ["Chlorophyll", "Melanin", "Keratin", "Hemoglobin"],
                       "correct_index": 0, "explanation": "Chlorophyll reflects green light."}],
            "written_questions": [{"question": "Explain why leaves appear green.", "type": "short",
                                     "answer_key": "Chlorophyll absorbs red/blue light and reflects green light.",
                                     "rubric": "1 pt for mentioning chlorophyll, 1 pt for correct light behavior."}],
        })

    if "learning-gap remediation plan" in prompt or '"title": "LearningGapsResponse"' in prompt:
        return json.dumps({
            "gaps": [{"misconception": "Plants get their food from soil.",
                       "diagnostic_question": "Ask: 'If plants eat soil, why doesn't the soil level in a pot decrease over time?'",
                       "severity": "medium",
                       "remedial_action": "Reference the classic Van Helmont willow tree experiment."}],
        })

    raise AssertionError(f"fake_chat got an unexpected prompt (first 200 chars): {prompt[:200]}")


llm_client.chat = fake_chat


def fake_is_configured():
    return True


_fake_vector_store = {}  # document_id -> list[str] chunks, standing in for Qdrant


def fake_ingest_chunks(db, document_id, chunks):
    _fake_vector_store[document_id] = list(chunks)


def fake_retrieve(db, document_id, query, k=5):
    return _fake_vector_store.get(document_id, [])[:k]


def fake_max_similarity_to_source(db, document_id, text):
    # Deterministic stand-in for cosine similarity: word-overlap ratio against
    # the best-matching chunk. Not a real embedding, just enough to exercise
    # the threshold logic in validator._check_hallucination with varying scores.
    chunks = _fake_vector_store.get(document_id, [])
    if not chunks or not text.strip():
        return 0.0
    text_words = set(text.lower().split())
    if not text_words:
        return 0.0
    best = 0.0
    for chunk in chunks:
        chunk_words = set(chunk.lower().split())
        if not chunk_words:
            continue
        overlap = len(text_words & chunk_words) / len(text_words)
        best = max(best, overlap)
    return best


def fake_delete_document_vectors(db, document_id):
    _fake_vector_store.pop(document_id, None)


vector_store.is_configured = fake_is_configured
vector_store.ingest_chunks = fake_ingest_chunks
vector_store.retrieve = fake_retrieve
vector_store.max_similarity_to_source = fake_max_similarity_to_source
vector_store.delete_document_vectors = fake_delete_document_vectors

# ---------------------------------------------------------------- Run it
from fastapi.testclient import TestClient  # noqa: E402
from app.main import app  # noqa: E402
from app import orchestrator  # noqa: E402
from app.database import init_db  # noqa: E402

init_db()
client = TestClient(app)

SAMPLE_TEXT = """Chapter 5: Photosynthesis

Photosynthesis is the process by which green plants and some other organisms
use sunlight to synthesize nutrients from carbon dioxide and water.

Chlorophyll, the green pigment found in chloroplasts, absorbs light energy
mainly in the red and blue wavelengths, reflecting green light -- which is
why most plants appear green.

The overall chemical equation for photosynthesis is:
6CO2 + 6H2O + light energy -> C6H12O6 + 6O2

This process is essential for life on Earth because it produces the oxygen
we breathe and forms the base of most food chains. A common misconception
among students is that plants absorb their food from the soil; in reality,
soil provides water and minerals, while the plant's own food (glucose) is
manufactured internally via photosynthesis.
"""
sample_path = os.path.join(TMP, "sample.txt")
with open(sample_path, "w") as f:
    f.write(SAMPLE_TEXT)

print("1) Uploading document...")
with open(sample_path, "rb") as f:
    r = client.post(
        "/api/documents/upload",
        files={"file": ("photosynthesis.txt", f, "text/plain")},
        data={"doc_type_hint": "mostly_text", "teaching_style": "hands-on, discussion-based",
              "time_constraints": "only 2 periods available"},
    )
assert r.status_code == 200, r.text
doc_id = r.json()["document_id"]
print(f"   -> document_id={doc_id}")

doc = client.get(f"/api/documents/{doc_id}").json()
print(f"   -> status after upload+background pipeline: {doc['status']}")
assert doc["status"] == "ready_for_planning", f"Expected ready_for_planning, got {doc}"
assert doc["classification"]["language"] == "English", "Schema-repair retry did not recover correctly"
print("   -> Stage 2 classification recovered correctly after a simulated schema-validation failure.")
print(f"   -> knowledge base concepts: {[c['name'] for c in doc['knowledge_base']['concepts']]}")

print("\n2) Walking Stages 4-8 (generate -> feedback -> regenerate -> approve)...")
for stage in orchestrator.STAGE_ORDER:
    r = client.post(f"/api/documents/{doc_id}/stages/{stage}/generate", json={})
    assert r.status_code == 200, f"{stage} generate failed: {r.text}"
    print(f"   -> {stage}: generated OK")

    if stage == "stage4_teaching_plan":
        r = client.post(f"/api/documents/{doc_id}/chat/{stage}", json={"message": "make it more hands-on"})
        assert r.status_code == 200, f"{stage} chat-feedback failed: {r.text}"
        print(f"   -> {stage}: chat feedback + regeneration OK")

    r = client.post(f"/api/documents/{doc_id}/stages/{stage}/approve")
    assert r.status_code == 200, f"{stage} approve failed: {r.text}"
    print(f"   -> {stage}: approved OK")

print("\n3) Publishing (Stage 9 validation + Stage 10 packaging)...")
r = client.post(f"/api/documents/{doc_id}/publish")
assert r.status_code == 200, r.text
package = r.json()
v = package["validation"]
print(f"   -> validation passed={v['passed']} hallucination_score={v['hallucination_score']} issues={len(v['issues'])}")

print("\n4) Exporting all formats...")
for fmt in ["json", "md", "html", "pdf", "docx"]:
    r = client.get(f"/api/documents/{doc_id}/export", params={"format": fmt})
    assert r.status_code == 200, f"export {fmt} failed: {r.text}"
    assert len(r.content) > 100, f"export {fmt} suspiciously small: {len(r.content)} bytes"
    print(f"   -> {fmt}: {len(r.content)} bytes OK")

print("\n5) Deleting document (single-doc lock reset)...")
r = client.delete(f"/api/documents/{doc_id}")
assert r.status_code == 200 and r.json()["deleted"] is True
r = client.get("/api/documents/active")
assert r.json()["active"] is False
print("   -> deleted, instance is clean, ready for a new upload.")

shutil.rmtree(TMP, ignore_errors=True)
print("\nALL CHECKS PASSED.")
