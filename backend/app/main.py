import hashlib
import csv
import io
import json
import os
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
import zipfile
from xml.etree.ElementTree import ParseError

from fastapi import FastAPI, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from PIL import Image, ImageStat, UnidentifiedImageError
from pydantic import Field, ValidationError

from backend.app.domain.contracts import (CalculationRequest, Contract, ImportOptions, LABELS,
    Observation, Preview, ProjectInput, Stage, Zone)
from backend.app.domain.evaluation import evaluate
from backend.app.domain.scene import summarize_scene
from backend.app.services.timeline_import import TimelineOptions, preview_timeline, template
from backend.app.services.import_schedule import MAX_UPLOAD, preview_import
from backend.app.services.scheduling import calculate
from backend.app.services.detector import DetectorUnavailable, detector_from_environment
from backend.app.services.stage_recognition import StageModelUnavailable, stage_classifier_from_environment
from backend.app.storage import Store

ROOT = Path(__file__).resolve().parents[2]


class Confirm(Contract):
    preview_id: str
    accept_warnings: bool = False


class CameraInput(Contract):
    name: str = Field(min_length=1, max_length=200)


class EvaluationInput(Contract):
    image_id: str
    schedule_id: str
    zone_id: str
    observation: Observation


class AnalysisInput(Contract):
    image_id: str
    schedule_id: str | None = None
    zone_id: str | None = None


class StagePredictionInput(Contract):
    image_id: str


def create_app(database_url=None, runtime=None, detector=None):
    runtime = Path(runtime or os.getenv("RUNTIME_DIR", str(ROOT/"runtime")))
    runtime.mkdir(parents=True, exist_ok=True)
    store = Store(database_url or os.getenv("DATABASE_URL", f"sqlite:///{runtime/'monitor.db'}"))
    @asynccontextmanager
    async def lifespan(app):
        store.initialize()
        yield
        store.engine.dispose()
    app = FastAPI(title="Мониторинг стройплощадки · P0", version="0.1.0", lifespan=lifespan)
    app.state.store = store
    try:
        app.state.detector = detector if detector is not None else detector_from_environment()
    except DetectorUnavailable as exc:
        app.state.detector_error = str(exc)
        app.state.detector = None
    else:
        app.state.detector_error = None
    try:
        app.state.stage_classifier = stage_classifier_from_environment()
    except StageModelUnavailable as exc:
        app.state.stage_classifier_error = str(exc)
        app.state.stage_classifier = None
    else:
        app.state.stage_classifier_error = None
    sources = json.loads((ROOT/"data/sources/registry.json").read_text(encoding="utf-8"))

    @app.exception_handler(KeyError)
    async def missing(_, exc):
        return JSONResponse(status_code=404, content={"detail":str(exc)})

    @app.exception_handler(ValueError)
    async def invalid(_, exc):
        return JSONResponse(status_code=422, content={"detail":str(exc)})

    @app.get("/api/health")
    def health():
        store.projects()
        return {"api":"ready", "database":"ready", "detector":"ready" if app.state.detector else "not_configured",
            "detector_error":app.state.detector_error, "worker":"synchronous",
            "stage_classifier":"ready" if app.state.stage_classifier else "not_configured",
            "stage_classifier_error":app.state.stage_classifier_error,
                "supported_classes":getattr(app.state.detector,"supported_classes",[]),
                "available_classes":getattr(app.state.detector,"available_classes",[]),
                "model_classes":getattr(app.state.detector,"model_classes",[]),
                "detector_profile":getattr(app.state.detector,"profile",None),
                "model_version":getattr(app.state.detector,"model_version",None), "phase":"P0 foundation"}

    @app.get("/api/equipment")
    def equipment():
        supported = getattr(app.state.detector,"supported_classes",[])
        available = getattr(app.state.detector,"available_classes",[])
        return [{"id":key, "name":name, "supported":key in supported,
                 "available":key in available} for key,name in LABELS.items()]

    @app.get("/api/sources")
    def source_registry():
        return sources

    @app.get("/api/projects")
    def projects():
        return store.projects()

    @app.post("/api/projects", status_code=201)
    def project(data: ProjectInput):
        return store.create_project(data.model_dump())

    @app.post("/api/projects/{pid}/cameras", status_code=201)
    def camera(pid: str, data: CameraInput):
        return store.save(pid,"camera",data.model_dump())

    @app.get("/api/projects/{pid}/cameras")
    def cameras(pid: str):
        return store.list(pid,"camera")

    @app.post("/api/projects/{pid}/zones", status_code=201)
    def zone(pid: str, data: Zone):
        store.get(pid,"camera",data.camera_id)
        return store.save(pid,"zone",data.model_dump(mode="json"))

    @app.get("/api/projects/{pid}/zones")
    def zones(pid: str):
        return store.list(pid,"zone")

    @app.post("/api/projects/{pid}/imports/preview")
    async def preview(pid: str, file: UploadFile=File(...), options: str=Form("{}")):
        store.project(pid)
        content = await file.read(MAX_UPLOAD+1)
        try:
            result = preview_import(content, file.filename or "", ImportOptions.model_validate_json(options))
        except (zipfile.BadZipFile, UnicodeDecodeError, OSError, ParseError, KeyError) as exc:
            raise ValueError("Не удалось прочитать файл CSV/XLSX") from exc
        return store.save(pid,"preview",result.model_dump(mode="json"))

    @app.post("/api/projects/{pid}/imports/confirm", status_code=201)
    def confirm(pid: str, data: Confirm):
        preview = store.get(pid,"preview",data.preview_id)
        if any(i["severity"]=="error" for i in preview["issues"]):
            raise ValueError("Исправьте ошибки перед сохранением")
        if preview["issues"] and not data.accept_warnings:
            raise ValueError("Подтвердите проверку предложенной иерархии и предупреждений")
        payload = {k:preview[k] for k in Preview.model_fields}
        payload["preview_id"] = data.preview_id
        payload["warnings_accepted"] = data.accept_warnings
        return store.save(pid,"import",payload)

    @app.get("/api/projects/{pid}/imports")
    def imports(pid: str, limit: int=Query(20,ge=1,le=100), offset: int=Query(0,ge=0)):
        return store.list(pid,"import",limit,offset)

    @app.post("/api/projects/{pid}/imports/{iid}/calculate")
    def calculation(pid: str, iid: str, data: CalculationRequest):
        imported = store.get(pid,"import",iid)
        for work in data.works:
            store.get(pid,"zone",work.zone_id)
            if work.provenance!="demonstration":
                raise ValueError("В первой версии параметры объекта не верифицированы; доступен демонстрационный расчёт")
        result = calculate(data,[Stage.model_validate(s) for s in imported["stages"]], {s["id"] for s in sources})
        result["import_id"] = iid
        return store.save(pid,"calculation_preview",result)

    @app.post("/api/projects/{pid}/schedules/confirm", status_code=201)
    def confirm_schedule(pid: str, data: Confirm):
        preview = store.get(pid,"calculation_preview",data.preview_id)
        if any(i['severity'] == 'error' for i in preview.get('issues', [])):
            raise ValueError('Исправьте ошибки сроков перед сохранением')
        if preview.get('issues') and not data.accept_warnings:
            raise ValueError('Подтвердите предупреждения предпросмотра сроков')
        payload = {k:v for k,v in preview.items() if k not in ("id","project_id","created_at")}
        if preview.get('method_version') == 'uploaded-calendar-v1':
            payload['warnings_accepted'] = data.accept_warnings
            payload['preview_id'] = data.preview_id
        return store.save(pid,"schedule",payload)

    @app.get('/api/projects/{pid}/imports/{iid}/timeline-template')
    def timeline_template(pid: str, iid: str, zone_id: str):
        imported = store.get(pid, 'import', iid)
        store.get(pid, 'zone', zone_id)
        return Response(template(imported['stages'], zone_id), media_type='text/csv; charset=utf-8',
                        headers={'Content-Disposition': 'attachment; filename="timeline.csv"'})

    @app.post('/api/projects/{pid}/imports/{iid}/timeline-preview')
    async def timeline_preview(pid: str, iid: str, file: UploadFile=File(...), options: str=Form('{}')):
        imported = store.get(pid, 'import', iid)
        content = await file.read(MAX_UPLOAD+1)
        # Resolve only supplied zone references without truncating to a list page.
        class ProjectZones:
            def __contains__(self, zid):
                try:
                    store.get(pid, 'zone', zid)
                    return True
                except KeyError:
                    return False
        try:
            result = preview_timeline(content, file.filename or '', imported, ProjectZones(),
                                      TimelineOptions.model_validate_json(options), store.project(pid)['timezone'])
        except csv.Error as exc:
            raise ValueError('Не удалось прочитать CSV: проверьте кавычки и длину полей') from exc
        return store.save(pid, 'calculation_preview', result)

    @app.get("/api/projects/{pid}/schedules")
    def schedules(pid: str, limit: int=Query(20,ge=1,le=100), offset: int=Query(0,ge=0)):
        return store.list(pid,"schedule",limit,offset)

    @app.post("/api/projects/{pid}/images", status_code=201)
    async def upload(pid: str, camera_id: str=Form(...), captured_at: str=Form(""),
                     time_source: str=Form("unknown"), file: UploadFile=File(...)):
        store.get(pid,"camera",camera_id)
        content = await file.read(MAX_UPLOAD+1)
        return save_image(pid, camera_id, captured_at, time_source, file.filename, content)

    def save_image(pid, camera_id, captured_at, time_source, filename, content):
        parsed_time = datetime.fromisoformat(captured_at) if captured_at else None
        observation = Observation(captured_at=parsed_time,time_source=time_source,model_version="not_configured",mode="model")
        if not content or len(content)>MAX_UPLOAD:
            raise ValueError("Снимок пуст или превышает 10 МБ")
        try:
            with Image.open(io.BytesIO(content)) as im:
                if im.format not in ("PNG","JPEG") or im.width*im.height>25_000_000:
                    raise ValueError("Нужен PNG/JPEG до 25 мегапикселей")
                im.load()
                width,height = im.size
                brightness = ImageStat.Stat(im.convert("L")).mean[0]
                quality = width>=320 and height>=240 and brightness>=20
                media = "image/png" if im.format=="PNG" else "image/jpeg"
        except (UnidentifiedImageError,OSError,Image.DecompressionBombError) as exc:
            raise ValueError("Снимок не декодируется") from exc
        digest = hashlib.sha256(content).hexdigest()
        target = runtime/"images"/digest
        target.parent.mkdir(exist_ok=True)
        # Content-addressed source; preserve bytes without trusting supplied filenames.
        try:
            with target.open("xb") as stream:
                stream.write(content)
        except FileExistsError:
            pass
        return store.save(pid,"image",{"sha256":digest,"filename":Path(filename or "image").name,
                 "camera_id":camera_id, "captured_at":observation.captured_at.astimezone(timezone.utc).isoformat() if observation.captured_at else None,
                 "time_source":time_source,"width":width,"height":height,"media_type":media,
                 "quality_heuristic_ok":quality,"brightness":round(brightness,2)})

    @app.post("/api/projects/{pid}/images/batch", status_code=201)
    async def upload_batch(pid: str, camera_id: str=Form(...), manifest: str=Form("{}"),
                           files: list[UploadFile]=File(...)):
        store.get(pid,"camera",camera_id)
        try:
            entries = json.loads(manifest)
            if not isinstance(entries, dict):
                raise ValueError
        except (json.JSONDecodeError, ValueError) as exc:
            raise ValueError("Manifest должен быть JSON-объектом по именам файлов") from exc
        if len(files)>100:
            raise ValueError("В одном пакете допускается не более 100 снимков")
        names = [Path(file.filename or "image").name for file in files]
        if len(names) != len(set(names)):
            raise ValueError("В пакете повторяются имена файлов; переименуйте их для однозначной привязки времени")
        accepted, errors = [], []
        for file in files:
            name = Path(file.filename or "image").name
            metadata = entries.get(file.filename or name, entries.get(name, {}))
            if not isinstance(metadata, dict):
                errors.append({"filename":name,"error":"Запись manifest должна быть объектом"})
                continue
            try:
                content = await file.read(MAX_UPLOAD+1)
                accepted.append(save_image(pid, camera_id, metadata.get("captured_at", ""),
                                           metadata.get("time_source", "unknown"), name, content))
            except (ValueError, TypeError) as exc:
                errors.append({"filename":name,"error":str(exc)})
        return {"accepted":accepted,"errors":errors,"total":len(files)}

    @app.get("/api/projects/{pid}/images")
    def images(pid: str, limit: int=Query(50,ge=1,le=100), offset: int=Query(0,ge=0)):
        return store.list(pid,"image",limit,offset)

    @app.get("/api/projects/{pid}/images/{iid}/file")
    def image_file(pid: str, iid: str):
        image = store.get(pid,"image",iid)
        return FileResponse(runtime/"images"/image["sha256"], media_type=image["media_type"])

    @app.post("/api/projects/{pid}/evaluations/fixture", status_code=201)
    def fixture(pid: str, data: EvaluationInput):
        if data.observation.mode!="fixture":
            raise ValueError("Этот endpoint принимает только явно тестовые детекции")
        project = store.project(pid)
        zone = store.get(pid,"zone",data.zone_id)
        image = store.get(pid,"image",data.image_id)
        schedule = store.get(pid,"schedule",data.schedule_id)
        if zone["camera_id"]!=image["camera_id"]:
            raise ValueError("Камера снимка не соответствует зоне")
        observation = data.observation.model_copy(update={
            "captured_at":datetime.fromisoformat(image["captured_at"]) if image["captured_at"] else None,
            "time_source":image["time_source"],
            "quality_ok":data.observation.quality_ok and image["quality_heuristic_ok"]})
        result = evaluate(observation,schedule,zone,project["timezone"])
        result['scene'] = summarize_scene(observation, zone, schedule, project['timezone'])
        result.update({"image_id":data.image_id,"schedule_id":data.schedule_id,
                       "observation":observation.model_dump(mode="json"),"zone_snapshot":zone,
                       "schedule_snapshot":schedule,"project_timezone":project["timezone"]})
        return store.save(pid,"evaluation",result)

    @app.post("/api/projects/{pid}/evaluations/analyze", status_code=201)
    def analyze(pid: str, data: AnalysisInput):
        if not app.state.detector:
            raise HTTPException(status_code=503, detail=app.state.detector_error or "Детектор не настроен")
        project = store.project(pid)
        zone = store.get(pid,"zone",data.zone_id) if data.zone_id else None
        image = store.get(pid,"image",data.image_id)
        schedule = store.get(pid,"schedule",data.schedule_id) if data.schedule_id else None
        if zone and zone["camera_id"]!=image["camera_id"]:
            raise ValueError("Камера снимка не соответствует зоне")
        try:
            batch = app.state.detector.detect(runtime/"images"/image["sha256"])
        except DetectorUnavailable as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        observation = Observation(captured_at=datetime.fromisoformat(image["captured_at"]) if image["captured_at"] else None,
                                  time_source=image["time_source"], quality_ok=image["quality_heuristic_ok"],
                                  supported_classes=batch.supported_classes, detections=batch.detections,
                                  model_detections=batch.model_detections,
                                  model_version=batch.model_version, mode="model",
                                  detection_confidence_floor=batch.detection_confidence_floor)
        result = evaluate(observation,schedule,zone,project["timezone"]) if schedule and zone else {
            'mode': 'model', 'model_version': observation.model_version, 'status': 'insufficient_data',
            'captured_at': observation.captured_at.isoformat() if observation.captured_at else None,
            'time_source': observation.time_source, 'zone_id': data.zone_id,
            'reason': 'Обзор кадра выполнен. Для проверки графика выберите график и зону.',
            'alerts': [], 'evaluations': []}
        result['scene'] = summarize_scene(observation, zone, schedule, project['timezone'])
        result.update({"image_id":image["id"],"schedule_id":data.schedule_id,
                       "observation":observation.model_dump(mode="json"),"zone_snapshot":zone,
                       "schedule_snapshot":schedule,"project_timezone":project["timezone"]})
        return store.save(pid,"evaluation",result)

    @app.post("/api/projects/{pid}/images/{iid}/stage-prediction", status_code=201)
    def stage_prediction(pid: str, iid: str, data: StagePredictionInput):
        if iid != data.image_id:
            raise ValueError("Идентификаторы снимка в пути и теле должны совпадать")
        if not app.state.stage_classifier:
            raise HTTPException(status_code=503, detail=app.state.stage_classifier_error or "Классификатор этапов не настроен")
        image = store.get(pid,"image",iid)
        try:
            prediction = app.state.stage_classifier.predict(runtime/"images"/image["sha256"])
        except StageModelUnavailable as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        return store.save(pid,"stage_prediction",{"image_id":iid,
                         "model_version":prediction.model_version,"label":prediction.label,
                         "confidence":prediction.confidence,"advisory":True,
                         "explanation":"Предсказание не выбирает активный этап графика автоматически"})

    @app.get("/api/projects/{pid}/evaluations")
    def evaluations(pid: str, limit: int=Query(50,ge=1,le=100), offset: int=Query(0,ge=0)):
        return store.list(pid,"evaluation",limit,offset)

    @app.get("/api/projects/{pid}/evaluations/{eid}")
    def evaluation(pid: str, eid: str):
        return store.get(pid,"evaluation",eid)

    @app.get("/")
    def index():
        return FileResponse(ROOT/"apps/web/index.html")

    app.mount("/assets",StaticFiles(directory=ROOT/"apps/web"),name="assets")
    return app


app = create_app()
