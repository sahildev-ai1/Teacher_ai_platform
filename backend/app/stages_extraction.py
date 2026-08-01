"""
Stage 2: Educational Classification
Stage 3: Knowledge Extraction (map-reduce over chunks)

Design notes (see also the client clarifications, doc index 1):
- Stage 2 is a GLOBAL question ("what is this document?") so it must NOT be
  answered via similarity retrieval -- we instead build one compressed
  representation of the whole document (headings + head + tail + a spread of
  sampled middle chunks) and classify from that in a single call.
- Stage 3 must scale to a full chapter without truncating content, so it runs
  as map-reduce: each chunk (batched a few at a time) is mined independently
  with the FAST model, then a REDUCE pass with the main model merges and
  de-duplicates everything into one KnowledgeBase. This is also what keeps
  Stage 9's grounding check meaningful -- every extracted item can be traced
  back to the batch of chunks it came from.
"""
import json
import logging
from typing import List

from sqlalchemy.orm import Session

from .config import settings
from .document_intelligence import ParsedDocument
from .llm_client import chat_structured
from .schemas import Classification, KnowledgeBase

logger = logging.getLogger("teacher_ai.stages_extraction")

DOC_TYPE_HINTS = {
    "mostly_text": "Mostly Text",
    "text_tables": "Text with Tables",
    "text_diagrams": "Text with Diagrams/Figures",
    "text_equations": "Text with Equations",
    "scanned": "Scanned PDF",
    "not_sure": "Not specified by user; infer from content",
}


def _compressed_context(parsed: ParsedDocument, max_chars: int = 6000) -> str:
    headings = "\n".join(f"- {h}" for h in parsed.structure.get("headings", [])[:40])
    chunks = parsed.chunks or [parsed.full_text]
    head = chunks[0] if chunks else ""
    tail = chunks[-1] if len(chunks) > 1 else ""
    middle = []
    if len(chunks) > 2:
        # spread samples across the middle so a 40-page chapter isn't judged
        # purely on its intro/conclusion
        step = max(1, (len(chunks) - 2) // 4)
        middle = chunks[1:-1:step][:4]
    body = "\n...\n".join([head] + middle + ([tail] if tail else []))
    combined = f"HEADINGS:\n{headings}\n\nCONTENT SAMPLE:\n{body}"
    return combined[:max_chars]


def classify_document(parsed: ParsedDocument, doc_type_hint: str = "not_sure") -> Classification:
    context = _compressed_context(parsed)
    prompt = f"""You are classifying an educational document for a teacher-facing platform.

User-provided hint about document structure: {DOC_TYPE_HINTS.get(doc_type_hint, doc_type_hint)}

Based ONLY on the content below, return a JSON object with EXACTLY these keys:
subject, grade, difficulty (Beginner|Intermediate|Advanced), topic, chapter,
category (e.g. STEM, Humanities, Language, Commerce), language,
doc_type (mostly_text|text_tables|text_diagrams|text_equations|scanned).

Infer grade level and difficulty from vocabulary, concept density, and framing
even if not explicitly stated. Do not invent a subject/topic not evidenced by
the text.

DOCUMENT:
{context}
"""
    return chat_structured(prompt, Classification, model=settings.ollama_model)


def _map_extract(chunk_batch: List[str], subject: str, grade: str) -> dict:
    joined = "\n---\n".join(chunk_batch)
    prompt = f"""You are extracting teachable knowledge from a {subject} textbook
excerpt for grade {grade}. From the TEXT below, extract ONLY what is explicitly
present or directly implied -- do not add outside facts.

Return JSON with keys: learning_objectives (list), prerequisites (list),
concepts (list of {{name, explanation}}), definitions (list of {{name, explanation}}),
formulae (list of strings, [] if none), keywords (list), examples (list),
applications (list), common_misconceptions (list of {{misconception, correction}}).
Use empty lists for anything not present in this excerpt -- the excerpts will
be merged later, so it's fine (and expected) for most fields to be partial.

TEXT:
{joined}
"""
    kb = chat_structured(prompt, KnowledgeBase, model=settings.ollama_fast_model)
    return kb.model_dump()


def _reduce_extract(partials: List[dict], subject: str, grade: str) -> KnowledgeBase:
    prompt = f"""You are merging several partial knowledge extractions of the
same {subject} chapter (grade {grade}) into ONE final structured knowledge base.

Merge the JSON objects below: de-duplicate items that mean the same thing,
combine near-duplicate concepts/definitions into a single clearer entry, and
keep every genuinely distinct learning objective/concept/definition/example.
Do NOT invent anything not present in the inputs.

Return JSON matching EXACTLY these keys: learning_objectives, prerequisites,
concepts (list of {{name, explanation}}), definitions (list of {{name, explanation}}),
formulae, keywords, examples, applications,
common_misconceptions (list of {{misconception, correction}}).

PARTIALS:
{json.dumps(partials, ensure_ascii=False)}
"""
    return chat_structured(prompt, KnowledgeBase, model=settings.ollama_model)


def extract_knowledge(
    parsed: ParsedDocument,
    subject: str,
    grade: str,
    batch_size: int = 3,
    progress_cb=None,
) -> KnowledgeBase:
    chunks = parsed.chunks or [parsed.full_text]
    batches = [chunks[i:i + batch_size] for i in range(0, len(chunks), batch_size)]
    partials = []
    for i, batch in enumerate(batches):
        partials.append(_map_extract(batch, subject, grade))
        if progress_cb:
            progress_cb(i + 1, len(batches))
    return _reduce_extract(partials, subject, grade)
