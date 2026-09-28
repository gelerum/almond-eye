$ErrorActionPreference = 'Stop'
Set-Location (Split-Path $PSScriptRoot -Parent)
$env:DETECTOR_PROFILE = 'construction-hazard'
$env:DETECTOR_MODEL = Join-Path (Get-Location) 'runtime/models/construction-hazard/yolo11n.pt'
$env:DETECTOR_CLASS_MAP = ''
$env:DETECTOR_VALIDATED_CLASSES = '[]'
& uv run uvicorn backend.app.main:app --host 127.0.0.1 --port 8000
exit $LASTEXITCODE
