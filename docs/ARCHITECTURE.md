# Almond Eye — архитектура решения

> Целевая архитектура прототипа. Все компоненты ниже реализованы (✅).
> Презентационная версия — в `docs/PRESENTATION.md`.

## 1. Задача и главный вопрос

**Вход:** календарный план объекта (этапы × сроки) и снимки камер с датой съёмки.
**Выход:** на выбранную дату — статус каждого этапа («в графике / отстаём на N дней / опережаем /
риск снижения темпа / нет данных»), общий вердикт по объекту и алерты с фото-доказательствами.

Ключевой принцип: **этап определяется по контексту**, то есть по сочетанию техники и материалов в кадре,
а не по одной машине.

## 2. Компоненты

```
                     ┌──────────────────────────────────────────────┐
  Пользователь ────► │ Web UI  apps/web (HTML/JS, без сборки)        │
                     │  • Статус объекта (вердикт, Гант, алерты)  ✅ │
                     │  • Загрузка плана XLSX / снимков с датой   ✅ │
                     │  • Настройка площадки, камер, зон           ✅ │
                     └───────────────────────┬──────────────────────┘
                                             │ REST / JSON
                     ┌───────────────────────▼──────────────────────┐
                     │ API  backend/app/main.py (FastAPI)            │
                     │  imports · images · analyze · status · jobs   │
                     └──┬──────────────┬───────────────┬────────────┘
                        │              │               │
      ┌─────────────────▼──┐  ┌────────▼─────────┐  ┌──▼──────────────────────┐
      │ Импорт планов       │  │ Детекторы (GPU)   │  │ Домен (чистые функции)   │
      │ services/           │  │ services/         │  │ domain/                  │
      │  gantt_import   ✅  │  │  detectors/   ✅  │  │  stage_inference   ✅    │
      │  timeline_import ✅ │  │   yolo, grounding,│  │  progress          ✅    │
      │  import_schedule ✅ │  │   clip verifier,  │  │  evaluation        ✅    │
      └─────────────────────┘  │   ensemble        │  │  scene             ✅    │
                               └────────┬──────────┘  └──────────┬──────────────┘
                                        │                        │
                     ┌──────────────────▼────────────────────────▼──────────┐
                     │ Хранилище  backend/app/storage.py                     │
                     │  SQLite (dev) / PostgreSQL (prod): projects + documents│
                     │  снимки: runtime/images/<sha256>                      │
                     │  датасеты и веса: runtime/, скрипты скачивания        │
                     └──────────────────────────────────────────────────────┘
  Конфигурация: backend/app/config/detectors.yaml (модели, пороги, слияние)
                backend/app/rules/stage_signatures.yaml (методика «этап ← техника»)
```

## 3. Поток данных

```
1. План XLSX ──► gantt_import ──► schedule {stages: [stage_key, name, start, end]}
2. Снимок + captured_at ──► image (sha256, качество кадра)
3. image ──► DetectionPipeline.detect()
             ├─ SiteSense YOLO26-L        → 8 классов техники
             ├─ Grounding DINO base       → буровые, гусеничные краны, бетононасосы, материалы
             │     └─ CLIP ViT-L/14        → проверка класса по кропу
             ├─ YOLO11s-seg (VT materials) → доля бетона / стали / профнастила
             ├─ YOLO11s (ConRebSeg)        → арматура
             └─ hazard YOLO11n             → люди (обзор сцены)
             ──► ensemble.fuse() ──► DetectionBatch {detections, materials, material_coverage}
4. DetectionBatch ──► stage_inference ──► [ {stage_key, score, explanation} ] ──► image_analysis
5. schedule + все image_analysis (≤ D) ──► progress ──► status {verdict, stages[], alerts[]}
```

## 4. Детекторы  (`backend/app/services/detectors/`)

| Файл | Назначение |
|---|---|
| `base.py` ✅ | `RawBox` (source, label, canonical, confidence, bbox), `AdapterResult`, протокол `Adapter` |
| `yolo.py` ✅ | адаптер Ultralytics (detect + segment); для сегментации считает долю кадра под классом |
| `grounding.py` ✅ | адаптер Grounding DINO (zero-shot) с группами промптов, fp16 autocast |
| `clip_verify.py` ✅ | переклассификация кропов DINO с помощью CLIP среди близких классов техники |
| `ensemble.py` ✅ | слияние: кластеры по IoU → noisy-OR уверенности → конфликты классов → пороги по классам |
| `pipeline.py` ✅ | сборка адаптеров из YAML, опциональные модели, `detect(path) → DetectionBatch` |

**Канонические метки** — значения `Equipment` или `material:<Material>` (`backend/app/domain/contracts.py`):

- Техника из ТЗ: самосвал, экскаватор, каток, кран-манипулятор, бетоносмеситель, бульдозер, грузовик, автокран.
- Расширение: башенный кран, гусеничный кран, буровая/сваебойная установка, погрузчик, автобетононасос.
- Материалы: арматура, опалубка, леса, трубы/шпунт, ж/б плиты, кладка, бетон, сталь, профнастил.

**Слияние** (`ensemble.fuse`):
1. Боксы одного класса с IoU ≥ 0.5 образуют кластер; от каждой модели в кластере учитывается один бокс.
2. Уверенность кластера `1 − Π(1 − w_s·c_s)`: согласие моделей повышает уверенность.
3. Взаимоисключающие классы (группы кранов, грузовиков, землеройной техники) при IoU ≥ 0.6: остаётся более уверенный.
4. Пороги по классам подбираются на нечётных кадрах, проверка — на чётных (`scripts/eval_detectors.py --tune`).

**Проверенные классы** (`validated_classes`): только для них разрешён алерт «нет обязательной
техники». Класс с низкой точностью не может вызвать ложную тревогу об отсутствии.

## 5. Методика «этап ← почерк техники»  (`backend/app/rules/stage_signatures.yaml`)

Для каждого этапа задаются:

| Поле | Смысл |
|---|---|
| `required` | OR-группы техники, все группы нужны для полного почерка (`[[excavator], [dump_truck]]`) |
| `supporting` | техника и материалы, повышающие уверенность, с весами |
| `contradicting` | признаки другой фазы, понижающие уверенность |
| `pace_critical` | техника, без которой этап идёт медленнее (самосвалы для котлована) |
| `plan_keywords` | подстроки названий в плане/перечне ЛТЦ для привязки строк плана к этапу |
| `order` | типичная последовательность этапов |

Этапы: подготовка территории → шпунт/сваи → котлован → фундамент → подземная часть →
установка башенного крана → надземная часть → фасад/кровля/окна → наружные сети → благоустройство →
демонтаж крана → ввод.

`domain/stage_inference.py` превращает найденную технику в ранжированный список этапов:
`score = выполненные required-группы + Σ веса supporting − Σ веса contradicting`, плюс
объяснение на русском («вижу экскаватор и самосвал; нет буровых → разработка котлована»).

## 6. Сравнение с планом  (`backend/app/domain/progress.py`)

Для даты D берутся снимки объекта с датой ≤ D. В доказательства идут этапы со score ≥ порога.

| Правило | Статус этапа |
|---|---|
| план закончен, но этап виден после `end` | `behind`, N = последнее наблюдение − end |
| план идёт, но виден только предыдущий этап | `behind`, N = D − planned start |
| этап виден раньше planned start | `ahead`, N = planned start − первое наблюдение |
| этап активен, но на свежих кадрах нет `pace_critical` техники | `at_risk` |
| в кадре техника, не нужная ни одному активному этапу | алерт `unexpected_equipment` |
| нет кадров в окне (45 дней при ежемесячной съёмке) | `no_data` |

**Вердикт по объекту** — худший статус по этапам и максимальная задержка, плюс одна строка
для руководителя. Каждый алерт содержит `image_ids`, то есть ссылки на снимки-доказательства.

## 7. Модель данных

Две таблицы (`storage.py`): `projects(id, name, timezone)` и
`documents(id, project_id, kind, fingerprint, payload JSON, created_at)`. Документы неизменяемы:
повторное сохранение того же содержимого возвращает существующую запись. Так сохраняется
полная история: какой моделью и по какому плану получен каждый вывод.

| kind | Содержимое |
|---|---|
| `camera`, `zone` | камера; полигон зоны работ (нормализованные координаты) |
| `schedule` / `plan` | этапы плана: stage_key, name, start, end, источник (xlsx/csv), sha256 файла |
| `image` | sha256, captured_at, time_source (user/camera/…), размеры, флаг качества |
| `image_analysis` | model_version, detections (класс, уверенность, bbox, модели-источники), materials, material_coverage, stages (ранжированные), top_stage |
| `evaluation` | покадровая проверка правил (обязательная / лишняя техника) |
| `job` | фоновый анализ пачки снимков: прогресс, ошибки |
| `status` | снимок результата `/status` на дату: вердикт, этапы, алерты |

## 8. API (основное)

| Метод | Путь | Что делает |
|---|---|---|
| POST | `/api/projects` | создать объект |
| POST | `/api/projects/{pid}/plans/xlsx/preview` · `/confirm` | импорт календарного плана XLSX |
| GET | `/api/projects/{pid}/plans` | версии плана |
| POST | `/api/projects/{pid}/images` · `/images/batch` | загрузка снимков, дата задаётся при загрузке |
| POST | `/api/projects/{pid}/images/{iid}/analyze-stage` | детекция и определение этапа по снимку |
| POST | `/api/projects/{pid}/analyze-all` → GET `/jobs/{jid}` | фоновый анализ всех новых снимков |
| GET | `/api/projects/{pid}/images/{iid}/analysis` | результат анализа снимка с боксами |
| GET | `/api/projects/{pid}/status?date=` | **вердикт, статусы этапов, наблюдения, алерты** |
| GET | `/api/health`, `/api/equipment` | состояние моделей; справочник техники и материалов |

Контракт ответа `/status`:

```json
{"project_id":"…","plan_id":"…","date":"2024-06-25",
 "verdict":{"status":"behind","delay_days":34,"summary":"Отстаём на 34 дня: …"},
 "stages":[{"stage_key":"excavation","name":"Разработка котлована","planned_start":"2024-04-01",
   "planned_end":"2024-05-31","planned_state":"should_be_done","observed_first":"2024-05-27",
   "observed_last":"2024-06-25","observations":7,"status":"behind","delay_days":25,"explanation":"…"}],
 "observations":[{"image_id":"…","captured_at":"2024-06-25","top_stage":"excavation","top_score":0.82,
   "stages":[{"stage_key":"excavation","score":0.82}],"equipment":{"excavator":2},"materials":{},
   "image_url":"/api/projects/…/images/…/file","analysis_id":"…"}],
 "alerts":[{"severity":"high","kind":"behind","stage_key":"excavation","message":"…","image_ids":["…"]}]}
```

## 9. Данные и модели

| Ресурс | Где | Лицензия |
|---|---|---|
| 100 снимков организатора, перечень работ ЛТЦ | `data/raw/dgp/` (`scripts/setup_data.py`, SHA-256) | материалы организатора |
| Ручная разметка: площадка, дата, техника, этап | `data/manifests/dgp_images.json` | наша |
| Демо-планы в формате шаблона | `data/plans/<site>.xlsx` | наши |
| SiteSense YOLO26-L | `runtime/models/sitesense` | MIT |
| Grounding DINO base | `runtime/models/grounding-dino-base` | Apache-2.0 |
| CLIP ViT-L/14 | HF cache | MIT |
| VT Structural Material → YOLO11s-seg | `runtime/models/structural-materials` | CC0 |
| ConRebSeg → YOLO11s (арматура) | `runtime/models/conrebseg-rebar` | CC BY 4.0 |
| Construction Hazard YOLO11n | `runtime/models/construction-hazard` | AGPL-3.0 |

Ревизии и SHA-256 скачиваемых весов закреплены в `data/sources/models.lock.json`
(`scripts/download_models.py`). Для обученных весов рядом лежит `train-manifest.json`
(данные, гиперпараметры, метрики).

## 10. Качество и воспроизводимость

- `scripts/eval_detectors.py` — точность и полнота по классам на 100 размеченных кадрах: по каждой модели и для ансамбля; пороги подбираются на нечётных кадрах, оцениваются на чётных.
- `scripts/eval_stage_inference.py` — точность определения этапа: на эталонной разметке техники (потолок методики) и на выходе детекторов.
- `scripts/seed_demo.py` — создаёт объекты по датированным площадкам, импортирует планы, загружает снимки и выводит вердикты.
- `pytest` — домен, импорт, API с фейковым детектором (без GPU).

## 11. Развёртывание

- `docker compose up`: FastAPI на порту 7860, GPU через CDI, `TORCH_DEVICE=0`, `DETECTOR_PROFILE=ensemble`.
- Без GPU: `TORCH_DEVICE=cpu`, в `detectors.yaml` можно выключить тяжёлые адаптеры
  (`enabled: false` у DINO/CLIP): остаётся SiteSense (≈0.4 с на кадр на CPU).
- Скорость на RTX 3060: SiteSense ≈0.07 с на кадр, полный ансамбль ≈3.3 с на кадр (DINO 2.7 с, CLIP 0.5 с).

## 12. Расширяемость

- **Новый тип техники:** значение в `Equipment` + фраза для DINO или класс дообученной YOLO + строки в методике.
- **Новый этап:** запись в `stage_signatures.yaml`; код не меняется.
- **Новый формат плана:** адаптер в `services/`, который выдаёт тот же `schedule`.
- **Новый источник снимков** (RTSP, облако камер): сервис, который кладёт кадры в `/images` с `time_source=camera`.
- **Масштаб:** очередь задач вместо BackgroundTasks, PostgreSQL, снимки в S3, несколько GPU-воркеров.
