#!/usr/bin/env bash
# Manual, real-server integration test: uploads a real file and walks the
# full pipeline with real Ollama calls (unlike test_pipeline_e2e.py, which
# uses a mocked LLM). Requires the backend to already be running with a
# valid OLLAMA_API_KEY in .env.
#
# Usage: ./manual_api_test.sh /path/to/chapter.pdf [base_url]
set -euo pipefail

FILE="${1:?Usage: ./manual_api_test.sh /path/to/chapter.pdf [base_url]}"
BASE="${2:-http://localhost:8000}"
STAGES=(stage4_teaching_plan stage5_classroom_content stage6_activities stage7_assessments stage8_learning_gaps)

jqd() { python3 -m json.tool; }  # pretty-print without requiring jq

echo "== 0) Health check =="
curl -sf "$BASE/healthz"; echo

echo "== 1) Upload =="
UPLOAD=$(curl -sf -X POST "$BASE/api/documents/upload" \
  -F "file=@${FILE}" \
  -F "doc_type_hint=mostly_text" \
  -F "teaching_style=activity-based" \
  -F "time_constraints=3 periods available")
echo "$UPLOAD" | jqd
DOC_ID=$(echo "$UPLOAD" | python3 -c "import json,sys; print(json.load(sys.stdin)['document_id'])")
echo "DOC_ID=$DOC_ID"

echo -e "\n== 2) Confirm single-document lock (expect 409) =="
curl -s -o /dev/null -w "second upload while active -> HTTP %{http_code}\n" \
  -X POST "$BASE/api/documents/upload" -F "file=@${FILE}"

echo -e "\n== 3) Stream Stage 1-3 progress (Ctrl+C once it prints waiting_user) =="
curl -N "$BASE/api/documents/$DOC_ID/stream"

echo -e "\n== 4) Document detail after Stage 1-3 =="
curl -sf "$BASE/api/documents/$DOC_ID" | jqd

for stage in "${STAGES[@]}"; do
  echo -e "\n== Stage: $stage — generate =="
  curl -sf -X POST "$BASE/api/documents/$DOC_ID/stages/$stage/generate" \
    -H "Content-Type: application/json" -d '{}' | jqd

  echo -e "\n== Stage: $stage — approve =="
  curl -sf -X POST "$BASE/api/documents/$DOC_ID/stages/$stage/approve" | jqd
done

echo -e "\n== 5) Publish (Stage 9 validation + Stage 10 packaging) =="
curl -sf -X POST "$BASE/api/documents/$DOC_ID/publish" | jqd

echo -e "\n== 6) Export all formats =="
for fmt in json md html pdf docx; do
  curl -sf "$BASE/api/documents/$DOC_ID/export?format=$fmt" -o "teacher_guide.$fmt"
  echo "  -> teacher_guide.$fmt ($(wc -c < teacher_guide.$fmt) bytes)"
done

echo -e "\n== 7) Delete / reset =="
curl -sf -X DELETE "$BASE/api/documents/$DOC_ID"; echo
curl -sf "$BASE/api/documents/active"; echo
