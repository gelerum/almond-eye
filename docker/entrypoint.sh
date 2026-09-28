#!/usr/bin/env bash
set -euo pipefail
cd /workspace

if [ "${DETECTOR_PROFILE:-}" = "construction-hazard" ] && python -c "import ultralytics" 2>/dev/null; then
    python scripts/download_hazard_model.py || echo "WARN: не удалось скачать веса детектора" >&2
fi
if [ "${DETECTOR_PROFILE:-}" = "ensemble" ] && python -c "import ultralytics, transformers" 2>/dev/null; then
    # Идемпотентно: уже скачанные файлы проверяются по SHA-256 и не качаются заново.
    python scripts/download_models.py || echo "WARN: не удалось скачать веса ансамбля" >&2
    python scripts/download_hazard_model.py || echo "WARN: не удалось скачать hazard-модель" >&2
fi

case "${1:-serve}" in
    serve)
        exec uvicorn backend.app.main:app --host 0.0.0.0 --port "${PORT:-8000}" ${UVICORN_ARGS:-}
        ;;
    claude)
        shift
        exec claude "$@"
        ;;
    *)
        exec "$@"
        ;;
esac
