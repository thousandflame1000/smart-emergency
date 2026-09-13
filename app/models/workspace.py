from sqlalchemy import Column, Integer, Text

from app.database import Base


class TopologyWorkspace(Base):
    __tablename__ = "topology_workspaces"

    id = Column(Text, primary_key=True)
    name = Column(Text, nullable=False)
    document = Column(Text, nullable=False)
    revision = Column(Integer, nullable=False, default=1)
    updated_at = Column(Text, nullable=False)
