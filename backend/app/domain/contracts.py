"""Versioned contracts shared by import, scheduling and observation evaluation."""
from datetime import date, datetime
from enum import StrEnum
from typing import Literal
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class Equipment(StrEnum):
    dump_truck = "dump_truck"
    excavator = "excavator"
    roller = "roller"
    loader_crane = "loader_crane"
    concrete_mixer = "concrete_mixer"
    bulldozer = "bulldozer"
    truck = "truck"
    mobile_crane = "mobile_crane"


LABELS = dict(zip(Equipment, ["Самосвал", "Экскаватор", "Каток", "Кран-манипулятор",
                             "Бетоносмеситель", "Бульдозер", "Грузовик", "Автокран"]))


class Stage(Contract):
    external_id: str = Field(min_length=1, max_length=180)
    parent_id: str | None = None
    source_row: int = Field(ge=1)
    name: str = Field(min_length=1)
    sequence_index: int = Field(ge=0)
    raw_number: str | None = None
    raw_values: list = Field(default_factory=list)
    object_types: list[str] = Field(default_factory=list)
    kind: Literal["summary", "work"] = "work"
    hierarchy_status: Literal["explicit", "proposed"] = "explicit"
    start_date: date | None = None
    end_date: date | None = None
    zone_ids: list[str] = Field(default_factory=list)
    stage_type: str | None = None

    @model_validator(mode="after")
    def dates(self):
        if bool(self.start_date) != bool(self.end_date):
            raise ValueError("Нужны обе даты или ни одной")
        if self.start_date and self.end_date < self.start_date:
            raise ValueError("Окончание раньше начала")
        return self


class ImportOptions(Contract):
    sheet: str | None = None
    header_row: int = Field(default=3, ge=1)
    id_column: int = Field(default=1, ge=1)
    name_column: int = Field(default=2, ge=1)
    parent_column: int | None = Field(default=None, ge=1)
    start_column: int | None = Field(default=None, ge=1)
    end_column: int | None = Field(default=None, ge=1)
    object_columns: list[int] = Field(default_factory=lambda: list(range(3, 12)))
    recover_date_numbers: bool = False


class Issue(Contract):
    severity: Literal["error", "warning"]
    code: str
    row: int | None = None
    message: str


class Preview(Contract):
    file_sha256: str
    sheet: str
    sheets: list[str]
    stages: list[Stage]
    issues: list[Issue]


class Rule(Contract):
    version: str
    required: list[list[Equipment]] = Field(min_length=1)
    allowed: list[Equipment] = Field(default_factory=list)
    confidence: float = Field(default=0.5, ge=0, le=1)

    @field_validator("required")
    @classmethod
    def nonempty_groups(cls, groups):
        if any(not group for group in groups):
            raise ValueError("Группа альтернатив не может быть пустой")
        return groups


class PlannedWork(Contract):
    stage_id: str
    zone_id: str
    volume: float = Field(ge=1e-9, le=1e10)
    volume_unit: Literal["m3", "m2", "t"] = "m3"
    output_per_shift: float = Field(ge=1e-9, le=1e10)
    output_unit: Literal["m3", "m2", "t"] = "m3"
    machines: int = Field(default=1, ge=1, le=1000)
    shifts_per_day: float = Field(default=1, ge=0.01, le=3)
    predecessors: list[str] = Field(default_factory=list)
    lag_days: int = Field(default=0, ge=0, le=3650)
    source_ids: list[str] = Field(min_length=1)
    assumptions: list[str] = Field(min_length=1)
    provenance: Literal["calculated", "demonstration"] = "demonstration"
    rule: Rule | None = None


class CalculationRequest(Contract):
    works: list[PlannedWork] = Field(min_length=1, max_length=1000)
    anchor: date | None = None
    anchor_source: Literal["unknown", "user", "demonstration"] = "unknown"
    weekdays: list[int] = Field(default_factory=lambda: [0, 1, 2, 3, 4])
    holidays: list[date] = Field(default_factory=list)

    @model_validator(mode="after")
    def calendar(self):
        if not self.weekdays or any(d not in range(7) for d in self.weekdays):
            raise ValueError("Укажите рабочие дни 0–6")
        if bool(self.anchor) != (self.anchor_source != "unknown"):
            raise ValueError("Дата привязки и её источник должны быть заданы вместе")
        return self


class BoundingBoxDetection(Contract):
    confidence: float = Field(ge=0, le=1)
    bbox: tuple[float, float, float, float]

    @field_validator("bbox")
    @classmethod
    def valid_box(cls, box):
        x1, y1, x2, y2 = box
        if not (0 <= x1 < x2 <= 1 and 0 <= y1 < y2 <= 1):
            raise ValueError("Рамка должна быть нормализована: 0 <= x1 < x2 <= 1")
        return box


class ModelDetection(BoundingBoxDetection):
    label: str = Field(min_length=1, max_length=200)


class Detection(BoundingBoxDetection):
    equipment: Equipment


class Zone(Contract):
    name: str = Field(min_length=1, max_length=200)
    camera_id: str
    polygon: list[tuple[float, float]] = Field(min_length=3, max_length=100)
    coverage_confirmed: bool = False

    @field_validator("polygon")
    @classmethod
    def valid_polygon(cls, points):
        if any(not (0 <= x <= 1 and 0 <= y <= 1) for x, y in points):
            raise ValueError("Координаты зоны должны быть от 0 до 1")
        if len(set(points)) != len(points):
            raise ValueError("Вершины не должны повторяться")
        area = sum(a[0]*b[1]-b[0]*a[1] for a,b in zip(points, points[1:]+points[:1]))
        if abs(area) < 1e-8:
            raise ValueError("Зона не должна иметь нулевую площадь")
        def cross(a,b,c):
            return (b[0]-a[0])*(c[1]-a[1])-(b[1]-a[1])*(c[0]-a[0])
        edges = list(zip(points, points[1:]+points[:1]))
        for i,(a,b) in enumerate(edges):
            for j,(c,d) in enumerate(edges):
                if j <= i+1 or (i==0 and j==len(edges)-1):
                    continue
                if cross(a,b,c)*cross(a,b,d) <= 0 and cross(c,d,a)*cross(c,d,b) <= 0:
                    if max(min(a[0],b[0]),min(c[0],d[0])) <= min(max(a[0],b[0]),max(c[0],d[0])) and max(min(a[1],b[1]),min(c[1],d[1])) <= min(max(a[1],b[1]),max(c[1],d[1])):
                        raise ValueError("Самопересечение полигона")
        return points


class ProjectInput(Contract):
    name: str = Field(min_length=1, max_length=200)
    timezone: str = "Europe/Moscow"

    @field_validator("timezone")
    @classmethod
    def valid_timezone(cls, value):
        try:
            ZoneInfo(value)
        except (KeyError, ValueError):
            raise ValueError("Неизвестный часовой пояс")
        return value


class Observation(Contract):
    captured_at: datetime | None = None
    time_source: Literal["unknown", "user", "camera", "demonstration"] = "unknown"
    quality_ok: bool = False
    supported_classes: list[Equipment] = Field(default_factory=list)
    detections: list[Detection] = Field(default_factory=list)
    model_detections: list[ModelDetection] = Field(default_factory=list)
    model_version: str
    mode: Literal["fixture", "model"]
    detection_confidence_floor: float = Field(default=0, ge=0, le=1)

    @model_validator(mode="after")
    def time(self):
        if self.captured_at and self.captured_at.utcoffset() is None:
            raise ValueError("Время снимка должно содержать часовой пояс")
        if bool(self.captured_at) != (self.time_source != "unknown"):
            raise ValueError("Время и источник времени должны быть заданы вместе")
        return self
