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
    # 純粹的整理用標籤（例如「本機測試」「演練」），跟 zone_id（地理分區，會限制
    # 自動派遣的媒合範圍）完全無關——刻意不共用同一欄位，才不會把「資料夾放哪」
    # 跟「這批物資在哪個地理分區可以被自動媒合」這兩件事綁在一起。
    folder = Column(Text, nullable=False, default="", server_default=text("''"))
