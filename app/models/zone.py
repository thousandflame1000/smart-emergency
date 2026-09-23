from sqlalchemy import Column, Float, Text, TIMESTAMP
from sqlalchemy.sql import func
from app.database import Base

# Every resource, need and workspace always has a zone_id; this is the one that
# always exists and that anything not explicitly assigned elsewhere falls back to.
GENERAL_ZONE_ID = "general"
GENERAL_ZONE_NAME = "未分區（預設）"


class Zone(Base):
    """A named partition of the operational picture (e.g. one neighbourhood/district).

    Resources, needs and workspaces each carry a zone_id so that matching and the
    workspace snapshot can be scoped to one zone instead of the whole platform.
    `general` is seeded once at startup (see app/services/zones.py) and can never
    be deleted — it is the fallback everything starts in before anyone organizes
    data into real zones, and it is what keeps single-zone deployments behaving
    exactly like the system did before zones existed.

    center_lat/center_lng/radius_km are optional: a zone without them can only be
    reached by an explicit reassignment, never by automatic lat/lng resolution
    (see resolve_zone_for_point). `general` deliberately has no center — it is a
    fallback, not a place on the map.
    """

    __tablename__ = "zones"

    id         = Column(Text, primary_key=True)
    name       = Column(Text, nullable=False)
    center_lat = Column(Float, nullable=True)
    center_lng = Column(Float, nullable=True)
    radius_km  = Column(Float, nullable=True)
    created_at = Column(TIMESTAMP(), server_default=func.now())
