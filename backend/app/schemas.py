"""
Pydantic schemas for every stage's output. These are the contract the LLM must
fill in (via structured JSON prompting) and what Stage 9 validates against.
Keeping them here (rather than inline in prompts) means the same schema is used
for: prompt instructions, `model_validate`-based validation, and the frontend's
expected JSON shape.
"""
from typing import List, Optional
from pydantic import BaseModel, Field


# ---------- Stage 2: Educational Classification ----------
class Classification(BaseModel):
    subject: str
    grade: str
    difficulty: str  # Beginner | Intermediate | Advanced
    topic: str
    chapter: str
    category: str  # e.g. STEM / Humanities / Language
    language: str
    doc_type: str  # mostly_text | text_tables | text_diagrams | text_equations | scanned


# ---------- Stage 3: Knowledge Extraction ----------
class Concept(BaseModel):
    name: str
    explanation: str
    source_ref: Optional[str] = None  # page/section it was grounded in


class Misconception(BaseModel):
    misconception: str
    correction: str


class KnowledgeBase(BaseModel):
    learning_objectives: List[str]
    prerequisites: List[str]
    concepts: List[Concept]
    definitions: List[Concept]
    formulae: List[str] = Field(default_factory=list)
    keywords: List[str]
    examples: List[str]
    applications: List[str]
    common_misconceptions: List[Misconception]


# ---------- Stage 4: Teaching Planner ----------
class PeriodPlan(BaseModel):
    period_number: int
    title: str
    duration_minutes: int
    objectives: List[str]
    concepts_covered: List[str]
    pacing_notes: str


class TeachingPlan(BaseModel):
    total_periods: int
    rationale: str  # why this many periods / this pacing
    periods: List[PeriodPlan]


# ---------- Stage 5: Classroom Content ----------
class PeriodContent(BaseModel):
    period_number: int
    entry_ticket: str
    teacher_script: str
    blackboard_notes: str
    classroom_activity_summary: str
    checkpoint_questions: List[str]
    exit_ticket: str
    homework: str
    mentor_moment: str


# ---------- Stage 6: Activities ----------
class Activity(BaseModel):
    period_number: int
    title: str
    type: str  # demonstration | role_play | experiment | discussion | game
    duration_minutes: int
    materials: List[str]
    instructions: List[str]
    success_criteria: List[str]


# ---------- Stage 7: Assessments ----------
class MCQ(BaseModel):
    question: str
    options: List[str]
    correct_index: int
    explanation: str


class WrittenQuestion(BaseModel):
    question: str
    type: str  # short | long | numerical
    answer_key: str
    rubric: str


class Assessment(BaseModel):
    period_number: int
    mcqs: List[MCQ]
    written_questions: List[WrittenQuestion]


# ---------- Stage 8: Learning Gap Analysis ----------
class LearningGap(BaseModel):
    misconception: str
    diagnostic_question: str
    severity: str  # low | medium | high
    remedial_action: str


class LearningGapsResponse(BaseModel):
    gaps: List[LearningGap]


# ---------- Stage 9: Validation ----------
class ValidationIssue(BaseModel):
    stage: str
    severity: str  # info | warning | error
    message: str


class ValidationReport(BaseModel):
    passed: bool
    issues: List[ValidationIssue]
    hallucination_score: float  # 0 = fully grounded risk, 1 = high risk
    schema_ok: bool
    consistency_ok: bool


# ---------- Stage 10: Final package ----------
class TeacherKnowledgePackage(BaseModel):
    document_id: str
    classification: Classification
    knowledge_base: KnowledgeBase
    teaching_plan: TeachingPlan
    period_content: List[PeriodContent]
    activities: List[Activity]
    assessments: List[Assessment]
    learning_gaps: List[LearningGap]
    validation: ValidationReport
