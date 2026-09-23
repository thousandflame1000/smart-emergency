from sqlalchemy import Column, ForeignKey, Integer, Text, text

from app.database import Base
from app.models.zone import GENERAL_ZONE_ID


class TopologyWorkspace(Base):
    __tablename__ = "topology_workspaces"

    id = Column(Text, primary_key=True)
    name = Column(Text, nullable=False)
    document = Column(Text, nullable=False)
    revision = Column(Integer, nullable=False, default=1)
    updated_at = Column(Text, nullable=False)
    zone_id = Column(Text, ForeignKey("zones.id"), nullable=False,
                     default=GENERAL_ZONE_ID, server_default=text(f"'{GENERAL_ZONE_ID}'"))
