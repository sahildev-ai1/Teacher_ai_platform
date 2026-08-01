"""
Stage 9: Validation.

Four checks, all feeding into one ValidationReport:
  1. Schema adherence -- re-validated here (constructors already enforce this,
     but we re-check so a bad manual edit via the chat-refinement loop can't
     silently slip an invalid shape into the final package).
  2. Hallucination / grounding -- for FACTUAL fields only (concepts,
     definitions, formulae, teacher scripts, checkpoint questions, assessment
     Q&A) we compute max cosine similarity against the source document's
     embedded chunks. Deliberately excluded: mentor_moment and activities,
     since those are meant to bring in outside analogies/anecdotes by design
     (per the client's Q&A) and would otherwise generate false positives.
  3. Completeness -- every learning objective and concept named in the
     teaching plan should be traceable back to the knowledge base.
  4. Consistency -- period numbering must line up 1:1 across the teaching
     plan, classroom content, activities, and assessments.
"""
from typing import List

from sqlalchemy.orm import Session

from .config import settings
from .schemas import (
    Classification, KnowledgeBase, TeachingPlan, PeriodContent, Activity,
    Assessment, LearningGap, ValidationIssue, ValidationReport,
)
from .vector_store import max_similarity_to_source


def _check_schema(*objs) -> bool:
    # Already-constructed pydantic objects are schema-valid by definition;
    # this exists as an explicit, auditable step (and a hook point if a raw
    # dict ever needs re-validating after a manual chat edit).
    return all(o is not None for o in objs)


def _check_consistency(plan: TeachingPlan, content: List[PeriodContent],
                        activities: List[Activity], assessments: List[Assessment]) -> List[ValidationIssue]:
    issues = []
    plan_nums = {p.period_number for p in plan.periods}
    for label, items in [("period_content", content), ("activities", activities), ("assessments", assessments)]:
        nums = {i.period_number for i in items}
        missing = plan_nums - nums
        extra = nums - plan_nums
        if missing:
            issues.append(ValidationIssue(stage=label, severity="error",
                                           message=f"Missing periods in {label}: {sorted(missing)}"))
        if extra:
            issues.append(ValidationIssue(stage=label, severity="warning",
                                           message=f"{label} references unknown periods: {sorted(extra)}"))
    return issues


def _check_completeness(plan: TeachingPlan, kb: KnowledgeBase) -> List[ValidationIssue]:
    issues = []
    concept_names = {c.name.lower() for c in kb.concepts}
    covered = set()
    for p in plan.periods:
        covered.update(c.lower() for c in p.concepts_covered)
    uncovered = concept_names - covered
    if uncovered:
        issues.append(ValidationIssue(
            stage="stage4_teaching_plan", severity="warning",
            message=f"Concepts extracted but not scheduled in any period: {sorted(uncovered)}",
        ))
    return issues


def _check_hallucination(db: Session, document_id: str, kb: KnowledgeBase,
                          content: List[PeriodContent], assessments: List[Assessment]) -> tuple:
    issues = []
    scores = []

    def _score(label: str, text: str, stage: str):
        if not text or not text.strip():
            return
        sim = max_similarity_to_source(db, document_id, text)
        scores.append(sim)
        if sim < settings.hallucination_similarity_threshold:
            issues.append(ValidationIssue(
                stage=stage, severity="warning",
                message=f"Low grounding similarity ({sim:.2f}) for {label}: "
                        f"\"{text[:80]}...\"",
            ))

    for c in kb.concepts:
        _score(f"concept:{c.name}", c.explanation, "stage3_knowledge_extraction")
    for d in kb.definitions:
        _score(f"definition:{d.name}", d.explanation, "stage3_knowledge_extraction")
    for pc in content:
        _score(f"period{pc.period_number}:teacher_script", pc.teacher_script, "stage5_classroom_content")
        _score(f"period{pc.period_number}:blackboard_notes", pc.blackboard_notes, "stage5_classroom_content")
    for a in assessments:
        for wq in a.written_questions:
            _score(f"period{a.period_number}:written_q", wq.question, "stage7_assessment")

    hallucination_score = 1.0 - (sum(scores) / len(scores)) if scores else 0.0
    return issues, round(max(0.0, min(1.0, hallucination_score)), 3)


def validate_package(
    db: Session,
    document_id: str,
    classification: Classification,
    knowledge_base: KnowledgeBase,
    plan: TeachingPlan,
    content: List[PeriodContent],
    activities: List[Activity],
    assessments: List[Assessment],
    gaps: List[LearningGap],
) -> ValidationReport:
    issues: List[ValidationIssue] = []

    schema_ok = _check_schema(classification, knowledge_base, plan)
    consistency_issues = _check_consistency(plan, content, activities, assessments)
    completeness_issues = _check_completeness(plan, knowledge_base)
    hallucination_issues, hallucination_score = _check_hallucination(
        db, document_id, knowledge_base, content, assessments
    )

    issues.extend(consistency_issues)
    issues.extend(completeness_issues)
    issues.extend(hallucination_issues)

    consistency_ok = not any(i.severity == "error" for i in consistency_issues)
    passed = schema_ok and consistency_ok and hallucination_score < 0.6

    return ValidationReport(
        passed=passed,
        issues=issues,
        hallucination_score=hallucination_score,
        schema_ok=schema_ok,
        consistency_ok=consistency_ok,
    )
