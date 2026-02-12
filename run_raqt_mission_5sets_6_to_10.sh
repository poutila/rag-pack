#!/usr/bin/env bash
set -euo pipefail

# Runs RAQT mission split sets 6..10 sequentially.
# Supports env overrides:
#   MODEL, BACKEND, PARQUET, INDEX, QUOTE_BYPASS_MODE, CACHE_PREFLIGHTS, OUT_ROOT
# Any CLI args passed to this script are forwarded to run_pack.py.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="${SCRIPT_DIR}"
cd "${REPO_ROOT}"

MODEL="${MODEL:-strand-iq4xs:latest}"
BACKEND="${BACKEND:-ollama}"
PARQUET="${PARQUET:-RAQT.parquet}"
INDEX="${INDEX:-.raqt.faiss}"
QUOTE_BYPASS_MODE="${QUOTE_BYPASS_MODE:-on}"
CACHE_PREFLIGHTS="${CACHE_PREFLIGHTS:-1}"
OUT_ROOT="${OUT_ROOT:-out/RAQT_MISSION_5SETS_6_TO_10_$(date +%y%m%d_%H%M%S)}"

PACK_DIR="."
PACKS=(
  "pack_rust_audit_raqt_mission_set6_v1_0.yaml"
  "pack_rust_audit_raqt_mission_set7_v1_0.yaml"
  "pack_rust_audit_raqt_mission_set8_v1_0.yaml"
  "pack_rust_audit_raqt_mission_set9_v1_0.yaml"
  "pack_rust_audit_raqt_mission_set10_v1_0.yaml"
)

mkdir -p "${OUT_ROOT}"

echo "== RAQT mission sets 6..10 batch run =="
echo "repo:        ${REPO_ROOT}"
echo "out root:    ${OUT_ROOT}"
echo "model:       ${MODEL}"
echo "backend:     ${BACKEND}"
echo "parquet:     ${PARQUET}"
echo "index:       ${INDEX}"
echo "quote mode:  ${QUOTE_BYPASS_MODE}"
echo "cache:       ${CACHE_PREFLIGHTS}"
echo

ok_count=0
fail_count=0
failed=()

for pack in "${PACKS[@]}"; do
  pack_path="${PACK_DIR}/${pack}"
  out_dir="${OUT_ROOT}/${pack%.yaml}"

  cmd=(
    uv run python ./run_pack.py
    --pack "${pack_path}"
    --parquet "${PARQUET}"
    --index "${INDEX}"
    --backend "${BACKEND}"
    --model "${MODEL}"
    --out-dir "${out_dir}"
    --quote-bypass-mode "${QUOTE_BYPASS_MODE}"
  )
  if [[ "${CACHE_PREFLIGHTS}" == "1" ]]; then
    cmd+=(--cache-preflights)
  fi
  if [[ "$#" -gt 0 ]]; then
    cmd+=("$@")
  fi

  echo "----"
  echo "[RUN] ${pack}"
  echo "[OUT] ${out_dir}"
  if "${cmd[@]}"; then
    echo "[OK]  ${pack}"
    ok_count=$((ok_count + 1))
  else
    echo "[FAIL] ${pack}"
    fail_count=$((fail_count + 1))
    failed+=("${pack}")
  fi
done

echo
echo "== Summary =="
echo "ok:   ${ok_count}"
echo "fail: ${fail_count}"
echo "root: ${OUT_ROOT}"
if [[ ${fail_count} -gt 0 ]]; then
  echo "failed packs:"
  for p in "${failed[@]}"; do
    echo "  - ${p}"
  done
  exit 1
fi

echo "all packs completed successfully."
