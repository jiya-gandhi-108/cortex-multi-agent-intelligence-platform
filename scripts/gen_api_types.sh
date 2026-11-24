#!/usr/bin/env bash
# Regenerate frontend/types/api.ts from the backend's OpenAPI schema.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT/backend"
.venv/bin/python -c "import json; from app.main import create_app; print(json.dumps(create_app().openapi()))" \
  > "$ROOT/frontend/types/openapi.json"
cd "$ROOT/frontend"
npx openapi-typescript types/openapi.json -o types/api.ts
