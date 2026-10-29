#!/bin/sh
# relay/sync-hud.sh — страница пульта это src/hud/view.html плюс фиксированный блок
# в <head> (viewport, title, manifest, source.js). Копия коммитится в
# relay/public/index.html и уезжает `wrangler deploy`; тест
# tests/hud/test_pult_copy.py проверяет, что копия равна источнику плюс этот блок.
set -eu
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
python3 - "$ROOT" <<'EOF'
import sys
from pathlib import Path

root = Path(sys.argv[1])
src = root / "src" / "hud" / "view.html"
dst = root / "relay" / "public" / "index.html"
sys.path.insert(0, str(root))
from src.hud.pult_copy import make_copy  # noqa: E402

dst.write_text(make_copy(src.read_text(encoding="utf-8")), encoding="utf-8")
print(f"{dst.relative_to(root)}: обновлён из {src.relative_to(root)}")
EOF
