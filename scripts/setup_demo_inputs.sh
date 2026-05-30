#!/usr/bin/env bash
#
# setup_demo_inputs.sh — re-stage Source A / Source B so the demo matches the
# inference pipeline's role convention (Source A = cands, Source B = index)
# and lock the demo to the two pipeline input files.
#
# Idempotent: running multiple times converges to the same state.
#
# What it does:
#   1. Move 10-248-580.city.json (and .prebaked.json) into Source A.
#   2. Move TheHague3D_Batch_07_Loosduinen_2022-08-08.json (and .prebaked.json) into Source B.
#   3. Archive every other file from Source A / Source B into _archive/<source>/.
#   4. Delete stale precomputed results (results_demo/demo_inference, data/property_dicts/*).
#
# Usage:
#   bash scripts/setup_demo_inputs.sh           # apply
#   bash scripts/setup_demo_inputs.sh --dry-run # preview only

set -euo pipefail

DRY_RUN=0
if [[ "${1:-}" == "--dry-run" ]]; then
  DRY_RUN=1
fi

run() {
  if [[ $DRY_RUN -eq 1 ]]; then
    printf '  [dry-run] %s\n' "$*"
  else
    printf '  %s\n' "$*"
    eval "$@"
  fi
}

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
HAGUE_DIR="$REPO_ROOT/data/RawCitiesData/The Hague"
SRC_A="$HAGUE_DIR/Source A"
SRC_B="$HAGUE_DIR/Source B"
ARCHIVE="$HAGUE_DIR/_archive"

CANDS_BASENAME="10-248-580.city.json"
INDEX_BASENAME="TheHague3D_Batch_07_Loosduinen_2022-08-08.json"

echo "==> setup_demo_inputs.sh"
echo "    repo root: $REPO_ROOT"
[[ $DRY_RUN -eq 1 ]] && echo "    DRY-RUN MODE"

# 1. Ensure directories exist.
run "mkdir -p \"$SRC_A\" \"$SRC_B\" \"$ARCHIVE/Source A\" \"$ARCHIVE/Source B\""

# Helper: relocate a file (and its .prebaked.json sibling) into a destination.
# If the file already lives in the destination, do nothing.
relocate() {
  local basename="$1" dest_dir="$2"
  # The basename may be either *.json or *.city.json — strip the extension once
  # to derive the prebaked sibling.
  local stem="${basename%.json}"
  for f in "$basename" "${stem}.prebaked.json"; do
    # Search both Source A and Source B for the file.
    for source_dir in "$SRC_A" "$SRC_B"; do
      local candidate="$source_dir/$f"
      if [[ -f "$candidate" ]]; then
        if [[ "$source_dir" == "$dest_dir" ]]; then
          : # already in the right place
        else
          run "mv \"$candidate\" \"$dest_dir/\""
        fi
      fi
    done
  done
}

# 2. Place the two locked files in their role-correct directories.
echo "==> placing locked input files"
relocate "$CANDS_BASENAME" "$SRC_A"
relocate "$INDEX_BASENAME" "$SRC_B"

# 3. Archive everything else from Source A / Source B (the locked files have
#    already been moved; whatever is left is unwanted).
archive_extras() {
  local source_dir="$1" arch_dir="$2"
  if [[ ! -d "$source_dir" ]]; then return 0; fi
  # Use find so spaces in filenames are handled correctly.
  find "$source_dir" -maxdepth 1 -type f -print0 |
    while IFS= read -r -d '' file; do
      local name; name="$(basename "$file")"
      # Skip the two locked files (and their .prebaked.json siblings).
      case "$name" in
        "$CANDS_BASENAME"|"${CANDS_BASENAME%.json}.prebaked.json") continue ;;
        "$INDEX_BASENAME"|"${INDEX_BASENAME%.json}.prebaked.json") continue ;;
      esac
      run "mv \"$file\" \"$arch_dir/\""
    done
}

echo "==> archiving unused files"
archive_extras "$SRC_A" "$ARCHIVE/Source A"
archive_extras "$SRC_B" "$ARCHIVE/Source B"

# 4. Wipe stale precomputed results (they were generated under the old role
#    assignment and would mislead the new live pipeline).
echo "==> removing stale precomputed results"
STALE_PATHS=(
  "$REPO_ROOT/results_demo/demo_inference"
  "$REPO_ROOT/data/property_dicts/features.parquet"
  "$REPO_ROOT/data/property_dicts/Hague_demo_130425_demo_inference_vector_normalization=True_seed=1.joblib"
)
for p in "${STALE_PATHS[@]}"; do
  if [[ -e "$p" ]]; then
    run "rm -rf \"$p\""
  fi
done

# 5. Ensure the cache root exists (Celery stage tasks will write here).
run "mkdir -p \"$REPO_ROOT/results_demo/cache\""

echo "==> done"
echo "    Source A: $(ls -1 "$SRC_A" 2>/dev/null | wc -l) file(s)"
echo "    Source B: $(ls -1 "$SRC_B" 2>/dev/null | wc -l) file(s)"
echo "    Archive : $(find "$ARCHIVE" -maxdepth 2 -type f 2>/dev/null | wc -l) file(s)"
