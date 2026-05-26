from sqlalchemy import Column, Text, TIMESTAMP
from sqlalchemy.types import TypeDecorator, String
from sqlalchemy.sql import func
import uuid
from app.database import Base


class GUID(TypeDecorator):
    """SQLite 相容的 UUID 型別（存成 Text，讀回 uuid.UUID）"""
    impl = String(36)
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        return str(value)

    def process_result_value(self, value, dialect):
        if value is None:
            return None
        try:
            return uuid.UUID(str(value))
        except Exception:
            return value

    @staticmethod
    def new() -> str:
        return str(uuid.uuid4())


class KnowledgeChunk(Base):
    __tablename__ = "knowledge_base"

    id         = Column(GUID(), primary_key=True, default=GUID.new)
    content    = Column(Text, nullable=False)
    embedding  = Column(Text, nullable=True)   # SQLite: JSON string；Postgres: vector(3072)
    source     = Column(Text, nullable=False)
    category   = Column(Text, nullable=False)
    created_at = Column(TIMESTAMP(), server_default=func.now())
