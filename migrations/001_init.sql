-- ============================================================
-- 鄰里守望平台 - Database Schema（歷史參考文件，已過時，不會被任何
-- 程式碼執行）
--
-- 這份檔案是專案早期規劃用 Supabase + pgvector 時寫的手動 migration，
-- 後來實際部署改成 Railway PostgreSQL，且資料表改成完全由
-- app/main.py 的 Base.metadata.create_all() 在啟動時自動建立
-- （SQLAlchemy model 定義才是目前的 schema 真相來源，見 app/models/）。
-- 這裡描述的 pgvector vector(3072) 欄位、roles TEXT[] 原生陣列型別，
-- 都跟現在實際跑的 schema 不一樣（roles/embedding 現在都是用
-- ArrayOfText/JSON 字串存在 TEXT 欄位裡，用 numpy cosine 做向量搜尋，
-- 不是 pgvector）。保留這份檔案只是留個歷史紀錄，不要照著這份文件
-- 手動建表。
-- ============================================================

CREATE EXTENSION IF NOT EXISTS vector;

-- ============================================================
-- 使用者
-- ============================================================
CREATE TABLE IF NOT EXISTS users (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    line_uid    TEXT UNIQUE,
    name        TEXT NOT NULL,
    phone       TEXT,
    roles       TEXT[] NOT NULL DEFAULT '{}',
    -- roles 可包含: 'elderly' | 'volunteer' | 'family' | 'admin'
    lat         FLOAT,
    lng         FLOAT,
    address     TEXT,
    is_active   BOOLEAN NOT NULL DEFAULT true,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- ============================================================
-- 關係：長者 ↔ 家屬 / 志工
-- ============================================================
CREATE TABLE IF NOT EXISTS care_relations (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    elderly_id      UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    contact_id      UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    relation        TEXT NOT NULL, -- 'family' | 'volunteer' | 'neighbor'
    notify_order    INT  NOT NULL DEFAULT 1,
    is_active       BOOLEAN NOT NULL DEFAULT true,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE(elderly_id, contact_id)
);

-- ============================================================
-- 每日打卡紀錄
-- ============================================================
CREATE TABLE IF NOT EXISTS daily_checkins (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    elderly_id      UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    date            DATE NOT NULL,
    status          TEXT NOT NULL DEFAULT 'pending',
    -- 'pending' | 'ok' | 'help_needed' | 'no_response' | 'confirmed_safe'
    note            TEXT,
    responded_at    TIMESTAMPTZ,
    confirmed_by    UUID REFERENCES users(id),
    confirmed_at    TIMESTAMPTZ,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE(elderly_id, date)
);

-- ============================================================
-- 警報通知紀錄
-- ============================================================
CREATE TABLE IF NOT EXISTS alerts (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    elderly_id      UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    checkin_id      UUID REFERENCES daily_checkins(id),
    alert_type      TEXT NOT NULL,
    -- 'no_response_1h' | 'no_response_3h' | 'help_needed' | 'emergency'
    notified_users  UUID[] NOT NULL DEFAULT '{}',
    status          TEXT NOT NULL DEFAULT 'sent',
    -- 'sent' | 'acknowledged' | 'resolved'
    resolved_by     UUID REFERENCES users(id),
    resolved_at     TIMESTAMPTZ,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- ============================================================
-- 社區物資登記
-- ============================================================
CREATE TABLE IF NOT EXISTS community_resources (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    owner_id        UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    resource_type   TEXT NOT NULL,
    -- 'water' | 'food' | 'first_aid' | 'shelter' | 'vehicle' | 'tool' | 'other'
    name            TEXT NOT NULL,
    quantity        TEXT,
    lat             FLOAT,
    lng             FLOAT,
    address         TEXT,
    is_available    BOOLEAN NOT NULL DEFAULT true,
    note            TEXT,
    valid_until     TIMESTAMPTZ,
    last_updated    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- ============================================================
-- 緊急需求（災時啟用）
-- ============================================================
CREATE TABLE IF NOT EXISTS community_needs (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    requester_id        UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    need_type           TEXT NOT NULL,
    description         TEXT,
    quantity            TEXT,
    lat                 FLOAT,
    lng                 FLOAT,
    address             TEXT,
    urgency             INT NOT NULL DEFAULT 2, -- 1=緊急 2=一般 3=低
    status              TEXT NOT NULL DEFAULT 'open',
    -- 'open' | 'matched' | 'fulfilled' | 'cancelled'
    matched_resource_id UUID REFERENCES community_resources(id),
    valid_until         TIMESTAMPTZ,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- ============================================================
-- RAG 知識庫
-- ============================================================
CREATE TABLE IF NOT EXISTS knowledge_base (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    content     TEXT NOT NULL,
    embedding   vector(3072),  -- Gemini gemini-embedding-001
    source      TEXT NOT NULL,
    category    TEXT NOT NULL,
    -- 'eldercare' | 'first_aid' | 'disaster' | 'volunteer_sop'
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS knowledge_base_embedding_idx
    ON knowledge_base
    USING ivfflat (embedding vector_cosine_ops)
    WITH (lists = 100);

-- ============================================================
-- 系統設定
-- ============================================================
CREATE TABLE IF NOT EXISTS system_config (
    key         TEXT PRIMARY KEY,
    value       TEXT NOT NULL,
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

INSERT INTO system_config (key, value) VALUES
    ('mode',                  'normal'),  -- 'normal' | 'emergency'
    ('checkin_hour',          '8'),
    ('checkin_minute',        '0'),
    ('alert_threshold_1_min', '60'),
    ('alert_threshold_2_min', '180')
ON CONFLICT (key) DO NOTHING;
