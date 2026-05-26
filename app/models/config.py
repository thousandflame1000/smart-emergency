from sqlalchemy import Column, Text, TIMESTAMP
from sqlalchemy.sql import func
from app.database import Base


class SystemConfig(Base):
    __tablename__ = "system_config"

    key        = Column(Text, primary_key=True)
    value      = Column(Text, nullable=False)
    updated_at = Column(TIMESTAMP(), server_default=func.now())
