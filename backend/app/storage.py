"""Immutable, scoped documents for the first P0 slice; SQLite locally/Postgres via URL."""
from datetime import datetime, timezone
import hashlib
import json
import uuid

from sqlalchemy import JSON, DateTime, ForeignKey, String, UniqueConstraint, create_engine, event, select
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column


class Base(DeclarativeBase):
    pass


class Project(Base):
    __tablename__ = "projects"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    timezone: Mapped[str] = mapped_column(String(100))


class Document(Base):
    __tablename__ = "documents"
    __table_args__ = (UniqueConstraint("project_id", "kind", "fingerprint"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), index=True)
    kind: Mapped[str] = mapped_column(String(30), index=True)
    fingerprint: Mapped[str] = mapped_column(String(64))
    payload: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class Store:
    def __init__(self, url):
        self.engine = create_engine(url, connect_args={"check_same_thread":False} if url.startswith("sqlite") else {})
        if url.startswith("sqlite"):
            @event.listens_for(self.engine, "connect")
            def foreign_keys(connection, _):
                connection.execute("PRAGMA foreign_keys=ON")

    def initialize(self):
        Base.metadata.create_all(self.engine)

    def project(self, project_id):
        with Session(self.engine) as session:
            p = session.get(Project, project_id)
            if not p:
                raise KeyError("Площадка не найдена")
            return {"id":p.id, "name":p.name, "timezone":p.timezone}

    def projects(self):
        with Session(self.engine) as session:
            return [{"id":p.id, "name":p.name, "timezone":p.timezone} for p in session.scalars(select(Project).order_by(Project.name))]

    def create_project(self, data):
        with Session(self.engine) as session, session.begin():
            p = Project(id=str(uuid.uuid4()), **data)
            session.add(p)
            return {"id":p.id, **data}

    def save(self, project_id, kind, payload):
        from sqlalchemy.exc import IntegrityError
        self.project(project_id)
        digest = hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
        def lookup(session):
            return session.scalar(select(Document).where(Document.project_id==project_id,
                    Document.kind==kind, Document.fingerprint==digest))
        with Session(self.engine) as session:
            old = lookup(session)
            if old:
                return self.serialize(old)
            doc = Document(id=str(uuid.uuid4()), project_id=project_id, kind=kind,
                           fingerprint=digest, payload=payload, created_at=datetime.now(timezone.utc))
            session.add(doc)
            try:
                session.commit()
            except IntegrityError:
                session.rollback()
                old = lookup(session)
                if not old:
                    raise
                return self.serialize(old)
            return self.serialize(doc)

    @staticmethod
    def serialize(doc):
        return {"id":doc.id, "project_id":doc.project_id, "created_at":doc.created_at.isoformat(), **doc.payload}

    def get(self, project_id, kind, doc_id):
        with Session(self.engine) as session:
            doc = session.get(Document, doc_id)
            if not doc or doc.project_id!=project_id or doc.kind!=kind:
                raise KeyError("Запись не найдена на этой площадке")
            return self.serialize(doc)

    def list(self, project_id, kind, limit=100, offset=0):
        self.project(project_id)
        with Session(self.engine) as session:
            docs = session.scalars(select(Document).where(Document.project_id==project_id,
                    Document.kind==kind).order_by(Document.created_at.desc(),Document.id).limit(limit).offset(offset))
            return [self.serialize(d) for d in docs]
