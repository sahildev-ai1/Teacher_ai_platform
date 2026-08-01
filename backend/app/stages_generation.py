"""
Stages 4-8: Teaching Planner, Classroom Content, Activities, Assessments,
Learning Gap Analysis.

Every generate_* function follows the same interactive pattern used by the
chat-refinement router (routers.py):
    1. First call: `feedback=None` -> generate from scratch.
    2. Teacher reviews in the chat panel, sends feedback -> the SAME function
       is called again with `feedback="<teacher's message>"` and `existing=
       <previous JSON>`, so the model revises rather than starting over.
The stage is only marked "approved" in the DB once the teacher says so
(handled by routers.py) -- nothing here decides when a stage is "done".

`pedagogy_search` (Tavily, optional) is used only inside Stages 4-6 for HOW to
teach (analogies, activity formats, pacing conventions) and its results are
explicitly labeled as secondary/inspirational in the prompt so the model
doesn't treat them as source facts -- consistent with the client's grounding
rule (secondary sources may shape pedagogy, never introduce new subject matter).
"""
import json
from typing import List, Optional

from .config import settings
from .llm_client import chat_structured, pedagogy_search
from .schemas import (
    Classification, KnowledgeBase, TeachingPlan, PeriodContent, Activity,
    Assessment, LearningGap, LearningGapsResponse,
)


def _feedback_block(feedback: Optional[str], existing: Optional[dict]) -> str:
    if not feedback:
        return ""
    return f"""
The teacher already reviewed a previous draft and gave this feedback:
"{feedback}"

PREVIOUS DRAFT (revise this, keep what the teacher didn't object to):
{json.dumps(existing, ensure_ascii=False)}
"""


# ---------------- Stage 4: Teaching Planner ----------------
def generate_teaching_plan(
    knowledge_base: KnowledgeBase,
    classification: Classification,
    time_constraints: Optional[str] = None,
    teaching_style: Optional[str] = None,
    feedback: Optional[str] = None,
    existing: Optional[dict] = None,
) -> TeachingPlan:
    prefs = []
    if time_constraints:
        prefs.append(f"Time constraint from teacher: {time_constraints}")
    if teaching_style:
        prefs.append(f"Preferred teaching style: {teaching_style}")
    prefs_block = "\n".join(prefs)

    prompt = f"""Design a multi-period teaching plan for grade {classification.grade}
{classification.subject}, topic "{classification.topic}" (difficulty:
{classification.difficulty}).

Decide the NUMBER of periods and each period's LENGTH yourself based on:
content volume ({len(knowledge_base.concepts)} concepts,
{len(knowledge_base.learning_objectives)} objectives), conceptual complexity,
and recommended pacing for this grade level. Do not default to a fixed
"5 periods x 40 minutes" template unless that genuinely fits the content.

{prefs_block}

LEARNING OBJECTIVES: {knowledge_base.learning_objectives}
CONCEPTS: {[c.name for c in knowledge_base.concepts]}
PREREQUISITES: {knowledge_base.prerequisites}
{_feedback_block(feedback, existing)}

Return JSON with keys: total_periods (int), rationale (string explaining your
pacing decision), periods (list of {{period_number, title, duration_minutes,
objectives (list), concepts_covered (list), pacing_notes}}).
"""
    return chat_structured(prompt, TeachingPlan, model=settings.ollama_model)


# ---------------- Stage 5: Classroom Content ----------------
def generate_period_content(
    plan: TeachingPlan,
    knowledge_base: KnowledgeBase,
    classification: Classification,
    feedback: Optional[str] = None,
    existing: Optional[dict] = None,
) -> List[PeriodContent]:
    results = []
    for period in plan.periods:
        strategy_ideas = pedagogy_search(
            f"{classification.subject} grade {classification.grade} {period.title}"
        )
        ideas_block = (
            "\nSECONDARY TEACHING-STYLE IDEAS (inspiration for HOW to teach only "
            "-- do NOT introduce any new facts/concepts from these):\n- " +
            "\n- ".join(strategy_ideas)
            if strategy_ideas else ""
        )
        existing_period = None
        if existing:
            existing_period = next(
                (p for p in existing.get("periods", []) if p.get("period_number") == period.period_number),
                None,
            )
        prompt = f"""Generate full classroom content for ONE period of a lesson.

Period {period.period_number}: "{period.title}" ({period.duration_minutes} min)
Objectives: {period.objectives}
Concepts covered: {period.concepts_covered}
Grade: {classification.grade}, Subject: {classification.subject}
{ideas_block}
{_feedback_block(feedback, existing_period)}

Return JSON with keys: period_number, entry_ticket (a short warm-up prompt),
teacher_script (what the teacher says/does, in teachable prose),
blackboard_notes (what should be written on the board, concise),
classroom_activity_summary (1-2 sentences), checkpoint_questions (list of 2-4),
exit_ticket, homework, mentor_moment (a short motivational anecdote or
real-world connection relevant to the topic).
"""
        content = chat_structured(prompt, PeriodContent, model=settings.ollama_model)
        content.period_number = period.period_number  # never trust the model's own numbering
        results.append(content)
    return results


# ---------------- Stage 6: Activities ----------------
def generate_activities(
    plan: TeachingPlan,
    knowledge_base: KnowledgeBase,
    classification: Classification,
    feedback: Optional[str] = None,
    existing: Optional[dict] = None,
) -> List[Activity]:
    results = []
    for period in plan.periods:
        ideas = pedagogy_search(f"classroom activity demonstration {period.title}")
        ideas_block = (
            "\nSecondary activity-format inspiration (adapt freely, don't copy facts):\n- " +
            "\n- ".join(ideas) if ideas else ""
        )
        existing_period = None
        if existing:
            existing_period = next(
                (a for a in existing.get("activities", []) if a.get("period_number") == period.period_number),
                None,
            )
        prompt = f"""Design ONE classroom activity for period {period.period_number}
("{period.title}", grade {classification.grade} {classification.subject}) that
reinforces: {period.concepts_covered}.
Choose the most fitting type: demonstration, role_play, experiment, discussion,
or game.
{ideas_block}
{_feedback_block(feedback, existing_period)}

Return JSON with keys: period_number, title, type, duration_minutes,
materials (list), instructions (ordered list of steps), success_criteria (list).
"""
        activity = chat_structured(prompt, Activity, model=settings.ollama_model)
        activity.period_number = period.period_number
        results.append(activity)
    return results


# ---------------- Stage 7: Assessments ----------------
def generate_assessments(
    plan: TeachingPlan,
    knowledge_base: KnowledgeBase,
    classification: Classification,
    feedback: Optional[str] = None,
    existing: Optional[dict] = None,
) -> List[Assessment]:
    results = []
    for period in plan.periods:
        existing_period = None
        if existing:
            existing_period = next(
                (a for a in existing.get("assessments", []) if a.get("period_number") == period.period_number),
                None,
            )
        prompt = f"""Create an assessment for period {period.period_number}
("{period.title}", grade {classification.grade} {classification.subject},
difficulty {classification.difficulty}) covering: {period.concepts_covered}.
Include a mix appropriate to the subject (use numerical questions only if the
subject is quantitative, e.g. Math/Physics/Chemistry/Accountancy).
{_feedback_block(feedback, existing_period)}

Return JSON with keys: period_number,
mcqs (list of {{question, options (list of 4), correct_index (0-3), explanation}}),
written_questions (list of {{question, type (short|long|numerical), answer_key, rubric}}).
Produce 3-5 mcqs and 2-4 written_questions.
"""
        assessment = chat_structured(prompt, Assessment, model=settings.ollama_model)
        assessment.period_number = period.period_number
        results.append(assessment)
    return results


# ---------------- Stage 8: Learning Gap Analysis ----------------
def generate_learning_gaps(
    knowledge_base: KnowledgeBase,
    classification: Classification,
    feedback: Optional[str] = None,
    existing: Optional[dict] = None,
) -> List[LearningGap]:
    prompt = f"""For grade {classification.grade} {classification.subject}
("{classification.topic}"), analyze this list of common misconceptions and
produce a learning-gap remediation plan.

MISCONCEPTIONS: {[m.model_dump() for m in knowledge_base.common_misconceptions]}
{_feedback_block(feedback, existing)}

Return JSON: {{"gaps": [{{misconception, diagnostic_question, severity
(low|medium|high), remedial_action}}]}}. If the misconceptions list is empty,
infer 2-3 plausible ones for this topic/grade from common student errors.
"""
    response = chat_structured(prompt, LearningGapsResponse, model=settings.ollama_model)
    return response.gaps
