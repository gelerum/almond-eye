# Almond Eye: успеваем ли мы по графику?

Прототип для хакатона ДГП Москвы. По снимкам камер система распознаёт строительную технику и определяет
этап работ **по сочетанию техники и материалов**. Затем сравнивает этапы с календарным планом и отвечает
по каждому этапу и по объекту в целом: «в графике», «отстаём на N дней», «опережаем» или «риск снижения
темпа». К ответу прилагаются алерты и снимки-доказательства.

- **Сопроводительная документация: [DOCUMENTATION.md](DOCUMENTATION.md)**. PDF для сдачи:
  `PROTOTYPE_ACCESS="<ссылка, логин, пароль>" uv run --no-sync --with reportlab python scripts/build_docs_pdf.py`
  (доступ попадает только в PDF, сам PDF в git не хранится)
- Архитектура: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)
- Материалы для презентации: [docs/PRESENTATION.md](docs/PRESENTATION.md)
- Методика «этап ← почерк техники» и результаты: [docs/STAGE_METHOD.md](docs/STAGE_METHOD.md)
- Качество детекторов: [docs/DETECTION_EVAL.md](docs/DETECTION_EVAL.md)

Всё ниже воспроизводится **с нуля**: из репозитория, открытых источников и архива организатора.
Личный DVC-remote не нужен.

---

## 1. Требования

| Что | Версия / примечание |
|---|---|
| ОС | Linux (проверено), macOS / Windows через Docker |
| Python | 3.12 (ставится через uv автоматически) |
| [uv](https://docs.astral.sh/uv/getting-started/installation/) | ≥ 0.5 |
| GPU | желательно NVIDIA ≥ 8 ГБ (проверено на RTX 3060 12 ГБ, CUDA 12.6); без GPU всё работает на CPU, только медленнее |
| Диск | ≈ 3 ГБ для запуска; +25 ГБ, если дообучать модели на открытых датасетах |
| Node.js | ≥ 18, только для тестов веб-интерфейса |

## 2. Установка

```bash
git clone <repo> construction-cv && cd construction-cv
uv sync --extra experiment --group dev      # FastAPI, torch (CUDA 12.6), ultralytics, transformers
export UV_NO_SYNC=1                         # важно: см. ниже
```

`--extra experiment` включает всё нужное для ансамбля детекторов. Без GPU команда та же: torch
работает на CPU, если задать `TORCH_DEVICE=cpu` (это значение по умолчанию).

**Зачем `UV_NO_SYNC=1`.** Без него каждый `uv run` перед запуском приводит окружение к составу по
умолчанию и **удаляет** extras (torch, ultralytics, transformers). Детекторы после этого не загрузятся.
Переменная действует до конца сессии терминала. Альтернатива — писать `uv run --no-sync …` в каждой команде.

## 3. Данные

### 3.1 Снимки организатора (обязательно)

Архив «7.ДГП_датасеты» (папка или zip «Строительная_техника» со 100 PNG и «Сводный перечень строительных
работ_ЛТЦ.xlsx») не входит в репозиторий. Возьмите его у организатора и разложите:

```bash
uv run scripts/setup_data.py --source ~/Downloads/7.ДГП_датасеты        # папка
uv run scripts/setup_data.py --source ~/Downloads/Строительная_техника.zip  # или zip
```

Скрипт копирует файлы в `data/raw/dgp/` и сверяет каждый снимок с эталонным SHA-256. Так разметка
`data/manifests/dgp_images.json` гарантированно относится к тем же пикселям.

### 3.2 Что уже лежит в репозитории

| Путь | Что это |
|---|---|
| `data/manifests/dgp_images.json` | ручная разметка 100 кадров: площадка, дата (со штампа в кадре), техника, наблюдаемый этап |
| `data/plans/site-*.xlsx` | календарные планы пяти площадок в формате шаблона организатора |
| `data/raw/templates/календарный план.pdf` | исходный шаблон плана |
| `data/sources/models.lock.json` | ревизии и SHA-256 скачиваемых моделей |
| `backend/app/config/detectors.yaml` | состав ансамбля, пороги, проверенные классы |
| `backend/app/rules/stage_signatures.yaml` | методика «этап ← техника» |

## 4. Модели

### 4.1 Готовые веса (обязательно для детекции)

```bash
uv run scripts/download_models.py            # SiteSense YOLO26-L, Grounding DINO base, CLIP ViT-L/14 (~2.7 ГБ)
uv run scripts/download_hazard_model.py      # люди/СИЗ для обзора сцены (опционально)
```

Файлы скачиваются в `runtime/models/<имя>/` с проверкой ревизии и SHA-256 из `data/sources/models.lock.json`.

### 4.2 Дообученные модели (опционально)

Без них приложение работает: адаптеры помечены `optional: true` и пропускаются, если весов нет.
Чтобы получить их самостоятельно:

```bash
uv run scripts/fetch_datasets.py structural-material     # 1.9 ГБ, CC0 (VT, мосты: бетон/сталь/профнастил)
uv run scripts/fetch_datasets.py conrebseg               # 22.6 ГБ, CC BY 4.0 (DTU, открытая арматура)

uv run scripts/train/prepare_structural_materials.py
uv run scripts/train/train_yolo.py --data runtime/datasets/structural-material/yolo/data.yaml \
    --model yolo11s-seg.pt --name structural-materials --imgsz 640 --batch 16 --epochs 60      # ≈1 ч на RTX 3060

uv run scripts/train/prepare_conrebseg.py
uv run scripts/train/train_yolo.py --data runtime/datasets/conrebseg/yolo/data.yaml \
    --model yolo11s.pt --name conrebseg-rebar --imgsz 960 --batch 8 --epochs 30                 # ≈1.5 ч
```

Лучшие веса копируются в `runtime/models/<name>/best.pt`. Рядом кладётся `train-manifest.json`
(данные, гиперпараметры, метрики). Скачивание докачивается с места обрыва и проверяется по MD5.

## 5. Запуск приложения

```bash
DETECTOR_PROFILE=ensemble TORCH_DEVICE=0 uv run uvicorn backend.app.main:app --host 0.0.0.0 --port 7860
# без GPU: TORCH_DEVICE=cpu
```

Откройте http://localhost:7860, по умолчанию откроется вкладка «Статус объекта».
Документация API: http://localhost:7860/docs. Состояние моделей: http://localhost:7860/api/health.

Интерфейс без бэкенда (демо-данные): http://localhost:7860/?mock=1.

### Docker

```bash
UID=$(id -u) GID=$(id -g) docker compose up -d --build     # http://localhost:7860
```

- Образ ставит зависимости ансамбля. При старте `docker/entrypoint.sh` докачивает модели, если их ещё нет.
- GPU пробрасывается через CDI (`nvidia.com/gpu=all`). Для машины без GPU уберите `devices:` и задайте `TORCH_DEVICE=cpu`.

## 6. Демо: пять площадок с планами

```bash
# быстрый прогон методики на эталонной разметке техники (без GPU, ~10 с):
uv run scripts/seed_demo.py --ground-truth --timeline --database sqlite:///runtime/gt.db

# полный прогон через детекторы в запущенном приложении (п. 5):
uv run scripts/seed_demo.py                    # API на localhost:7860
```

Скрипт создаёт по объекту на площадку, импортирует план из `data/plans/`, загружает снимки с датами из
разметки, запускает анализ и печатает вердикты. Потом откройте веб-интерфейс и выберите объект.

Как работать с интерфейсом вручную:
1. «+ Новый объект» → название → календарный план XLSX (есть «Скачать пример плана») → снимки с **датой съёмки** (одна на пачку). Анализ запускается сам.
2. Откроется страница объекта: вердикт, сводка по этапам, алерты с фото, Гант план/факт. Клик по точке или миниатюре открывает снимок с рамками.
3. Новые данные — кнопками «+ Снимки» и «Обновить план». Инженерная консоль — «Расширенные настройки» внизу меню.

## 7. Проверка качества

```bash
uv run scripts/eval_detectors.py --rerun --tune   # детекторы: P/R по классам (~15 мин на GPU), отчёт runtime/eval/report.json
uv run scripts/eval_stage_inference.py            # точность определения этапа (эталон и детекторы)
uv run pytest -q                                  # домен, импорт, API с фейковым детектором (без GPU)
node --test tests/web/                            # веб-интерфейс
```

Пороги по классам подбираются на нечётных кадрах (`--tune`), а качество меряется на чётных.

## 8. Структура

```
backend/app/
  main.py                    REST API (FastAPI)
  config/detectors.yaml      ансамбль детекторов
  rules/stage_signatures.yaml методика «этап ← техника»
  domain/                    stage_inference, progress, evaluation, scene, contracts
  services/detectors/        yolo, grounding (DINO), clip_verify, ensemble, pipeline
  services/gantt_import.py   импорт календарного плана XLSX/CSV
  storage.py                 SQLite/PostgreSQL (DATABASE_URL)
apps/web/                    интерфейс (HTML/JS без сборки)
scripts/                     данные, модели, обучение, оценка, демо
data/                        разметка, планы, источники (снимки организатора — через setup_data.py)
docs/                        архитектура, презентация, методика, оценка
```

## 9. Лицензии данных и моделей

| Ресурс | Лицензия |
|---|---|
| SiteSense YOLO26-L (`Zaafan/sitesense-weights`) | MIT |
| Grounding DINO base (`IDEA-Research/grounding-dino-base`) | Apache-2.0 |
| CLIP ViT-L/14 (`openai/clip-vit-large-patch14`) | MIT |
| VT Structural Material Segmentation Dataset | CC0 |
| ConRebSeg (self-collected part) | CC BY 4.0 |
| Construction Hazard Detection YOLO11n | AGPL-3.0 |
| Снимки и перечень работ организатора | материалы хакатона, в репозиторий не входят |

## 10. Частые проблемы

- **`/api/health` показывает `skipped: weights missing`.** Опциональная модель ещё не обучена. Это нормально, см. п. 4.2.
- **CUDA out of memory.** Уменьшите `imgsz` у SiteSense в `detectors.yaml` или выключите `gdino` (`enabled: false`); на CPU задайте `TORCH_DEVICE=cpu`.
- **Нет интернета при запуске.** Все модели читаются из `runtime/models`: скачайте их заранее (п. 4.1).
- **`ModuleNotFoundError: transformers` / `ultralytics`.** Окружение пересинхронизировалось без extras.
  Повторите `uv sync --extra experiment --group dev` и задайте `export UV_NO_SYNC=1` (п. 2).
- **`setup_data.py`: «Отличаются от эталона».** У вас другая версия архива организатора. Такие файлы
  переименовываются в `*.mismatch`, а разметка к ним не применяется.

История проекта: README первой итерации (P0, профиль Construction Hazard) — [docs/P0_README.md](docs/P0_README.md).
