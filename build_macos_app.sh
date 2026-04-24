#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

export PYINSTALLER_CONFIG_DIR="$SCRIPT_DIR/.pyinstaller"

STAGE_DIST="$SCRIPT_DIR/.dist_stage"
STAGE_BUILD="$SCRIPT_DIR/.build_stage"
FINAL_DIST="$SCRIPT_DIR/dist"

rm -rf "$STAGE_DIST" "$STAGE_BUILD"

python3 "$SCRIPT_DIR/scripts/generate_app_icon.py"
python3 -m PyInstaller \
  --noconfirm \
  --distpath "$STAGE_DIST" \
  --workpath "$STAGE_BUILD" \
  Source.spec

mkdir -p "$FINAL_DIST"
rm -rf "$FINAL_DIST/Source.app" "$FINAL_DIST/Source"
ditto "$STAGE_DIST/Source.app" "$FINAL_DIST/Source.app"

echo
echo "Built app bundle:"
echo "  $SCRIPT_DIR/dist/Source.app"
