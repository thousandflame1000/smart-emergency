# -*- coding: utf-8 -*-
"""
Railway / 生產環境啟動腳本
自動建表 → seed demo 資料 → 載入知識庫
只在資料庫是空的時候執行（冪等）
"""
import sys, os
sys.path.insert(0, os.path.dirname(__file__))

from app.database import engine, Base, SessionLocal
from app.models import user, care_relation, checkin, alert, resource, need, config, knowledge

def run():
    print("[startup] 建立資料表...")
    Base.metadata.create_all(bind=engine)

    db = SessionLocal()
    from app.models.user import User

    if db.query(User).count() > 0:
        print("[startup] 資料庫已有資料，跳過 seed")
        db.close()
        return

    db.close()

    print("[startup] 空資料庫，執行 seed...")
    import seed
    seed.run()

    print("[startup] 載入知識庫...")
    import ingest_kb
    ingest_kb.run()

    print("[startup] 完成！")

if __name__ == "__main__":
    run()
