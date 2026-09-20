# -*- coding: utf-8 -*-
"""網頁表單（有真正文字框）：簽章連結、身分驗證、與聊天指令相同的建立邏輯。"""
import time

import pytest
from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.testclient import TestClient
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.config import settings
from app.errors import http_exception_handler, validation_error_handler
from app.models.need import CommunityNeed
from app.models.resource import CommunityResource
from app.models.user import User
from app.models.volunteer_application import VolunteerApplication
from app.routers import webform
from app.services import form_token


@pytest.fixture()
def client():
    app = FastAPI()
    app.add_exception_handler(StarletteHTTPException, http_exception_handler)
    app.add_exception_handler(RequestValidationError, validation_error_handler)
    app.include_router(webform.router, prefix="/f")
    return TestClient(app)


def mk(db, name, roles, uid, **kw):
    u = User(name=name, roles=roles, line_uid=uid, **kw)
    db.add(u); db.commit(); db.refresh(u)
    return u


# ── 簽章連結 ──
def test_token_round_trip_and_tampering():
    token = form_token.make_token("U-abc")
    assert form_token.verify_token(token) == "U-abc"
    payload, sig = token.split(".")
    forged = form_token._b64(b'{"u":"U-admin","e":9999999999}') + "." + sig
    assert form_token.verify_token(forged) is None
    assert form_token.verify_token(payload + ".AAAA") is None
    assert form_token.verify_token("garbage") is None
    assert form_token.verify_token("") is None


def test_token_expires():
    token = form_token.make_token("U-abc", now=time.time() - form_token.TOKEN_TTL_SECONDS - 5)
    assert form_token.verify_token(token) is None


def test_form_url_points_at_public_base(monkeypatch):
    monkeypatch.setattr(settings, "PUBLIC_BASE_URL", "https://example.test/")
    assert form_token.form_url("need", "U-x").startswith("https://example.test/f/need?t=")


# ── 頁面與情境 ──
def test_pages_served_and_unknown_kind_404(client):
    for kind in ("need", "res", "apply"):
        r = client.get(f"/f/{kind}")
        assert r.status_code == 200 and "<form" in r.text
    assert client.get("/f/bogus").status_code == 404


def test_context_requires_valid_token_and_prefills(db, client):
    mk(db, "王小明", ["elderly"], "U-ctx", phone="0912345678", address="台中市南區")
    assert client.get("/f/api/context", params={"t": "bad.token"}).status_code == 401
    r = client.get("/f/api/context", params={"t": form_token.make_token("U-ctx")})
    assert r.status_code == 200
    assert r.json()["name"] == "王小明" and r.json()["is_staff"] is False
    assert client.get("/f/api/context", params={"t": form_token.make_token("U-unknown")}).status_code == 404


def test_inactive_user_is_refused(db, client):
    mk(db, "停用", ["elderly"], "U-off", is_active=False)
    r = client.post("/f/api/need", json={"t": form_token.make_token("U-off"), "types": ["water"]})
    assert r.status_code == 403


# ── 申請物資 ──
def test_need_form_creates_needs_updates_profile_and_notifies(db, client, line_outbox, monkeypatch):
    from app.services import places
    monkeypatch.setattr(places, "geocode_address", lambda a: (24.13, 120.66))
    mk(db, "LINE暱稱", ["elderly"], "U-need")
    r = client.post("/f/api/need", json={
        "t": form_token.make_token("U-need"), "name": "王小明", "phone": "0912-345-678",
        "address": "台中市南區崇倫街88號", "types": ["water", "first_aid", "water"],
        "people": 3, "urgent": True, "note": "樓梯很陡",
    })
    assert r.status_code == 200, r.text
    db.expire_all()
    user = db.query(User).filter(User.line_uid == "U-need").one()
    assert (user.name, user.phone, user.address) == ("王小明", "0912-345-678", "台中市南區崇倫街88號")
    assert (user.lat, user.lng) == (24.13, 120.66)
    needs = {n.need_type: n for n in db.query(CommunityNeed).all()}
    assert set(needs) == {"water", "first_aid"}
    assert needs["water"].urgency == 4
    assert "3人" in needs["water"].description and "樓梯很陡" in needs["water"].description
    assert needs["water"].address == "台中市南區崇倫街88號"
    assert any("已登記您的需求" in t for t in line_outbox.texts("U-need"))


def test_need_form_validation(db, client):
    mk(db, "長者", ["elderly"], "U-v")
    t = form_token.make_token("U-v")
    assert client.post("/f/api/need", json={"t": t, "types": []}).status_code == 422
    assert client.post("/f/api/need", json={"t": t, "types": ["hacker"]}).status_code == 422
    assert client.post("/f/api/need", json={"t": t, "types": ["water"], "phone": "abc"}).status_code == 422
    assert client.post("/f/api/need", json={"t": t, "types": ["water"], "people": 0}).status_code == 422
    assert client.post("/f/api/need", json={"t": t, "types": ["water"], "note": "x" * 501}).status_code == 422
    assert db.query(CommunityNeed).count() == 0


def test_need_form_cannot_impersonate_another_user(db, client):
    mk(db, "甲", ["elderly"], "U-a")
    mk(db, "乙", ["elderly"], "U-b")
    client.post("/f/api/need", json={"t": form_token.make_token("U-a"), "types": ["water"]})
    owners = {n.requester.line_uid for n in db.query(CommunityNeed).all()}
    assert owners == {"U-a"}


# ── 登記物資 ──
def test_resource_form_staff_only_and_saves_fields(db, client, monkeypatch):
    from app.services import places
    monkeypatch.setattr(places, "geocode_address", lambda a: (24.15, 120.68))
    mk(db, "民眾", ["elderly"], "U-r-no")
    r = client.post("/f/api/resource", json={"t": form_token.make_token("U-r-no"), "rtype": "water", "quantity": "5箱"})
    assert r.status_code == 403 and db.query(CommunityResource).count() == 0

    mk(db, "志工", ["volunteer"], "U-r-ok")
    r = client.post("/f/api/resource", json={
        "t": form_token.make_token("U-r-ok"), "rtype": "food", "quantity": "50份",
        "resource_name": "熱食便當", "address": "台中市西區水湳路99號"})
    assert r.status_code == 200, r.text
    res = db.query(CommunityResource).one()
    assert (res.resource_type, res.quantity, res.name, res.address) == ("food", "50份", "熱食便當", "台中市西區水湳路99號")
    assert (res.lat, res.lng) == (24.15, 120.68)


def test_resource_form_rejects_bad_type_and_missing_quantity(db, client):
    mk(db, "志工", ["volunteer"], "U-r2")
    t = form_token.make_token("U-r2")
    assert client.post("/f/api/resource", json={"t": t, "rtype": "sos", "quantity": "1"}).status_code == 422
    assert client.post("/f/api/resource", json={"t": t, "rtype": "water", "quantity": "   "}).status_code == 422
    assert db.query(CommunityResource).count() == 0


# ── 志工申請 ──
def test_apply_form_creates_pending_application_once(db, client, line_outbox):
    mk(db, "LINE暱稱", ["elderly"], "U-ap")
    t = form_token.make_token("U-ap")
    r = client.post("/f/api/apply", json={"t": t, "name": "陳小美", "phone": "0912345678", "service_area": "南區"})
    assert r.status_code == 200, r.text
    assert client.post("/f/api/apply", json={"t": t, "name": "陳小美"}).status_code == 200
    rows = db.query(VolunteerApplication).all()
    assert len(rows) == 1 and (rows[0].name, rows[0].service_area, rows[0].status) == ("陳小美", "南區", "pending")
    assert client.post("/f/api/apply", json={"t": t, "name": "  "}).status_code == 422


def test_apply_form_refused_for_existing_volunteer(db, client):
    mk(db, "志工", ["volunteer"], "U-ap2")
    r = client.post("/f/api/apply", json={"t": form_token.make_token("U-ap2"), "name": "志工"})
    assert r.status_code == 409


# ── 展演密碼閘不能擋住民眾的表單 ──
def test_demo_password_does_not_block_forms_but_blocks_admin(monkeypatch):
    from app.main import app
    monkeypatch.setattr(settings, "DEMO_PASSWORD", "secret")
    c = TestClient(app)
    assert c.get("/f/need").status_code == 200
    assert c.get("/admin").status_code == 401


# ── 機器人端 ──
def test_bot_entry_commands_send_a_signed_link(db, line_outbox):
    from tests.test_line_hardening import say
    mk(db, "長者", ["elderly"], "U-bot", lat=23.9, lng=121.6)
    say("U-bot", "申請物資")
    card = str(line_outbox.sent[-1][2].contents.to_dict())
    assert "/f/need?t=" in card
    token = card.split("/f/need?t=")[1].split("'")[0]
    assert form_token.verify_token(token) == "U-bot"
    mk(db, "志工", ["volunteer"], "U-bot2", lat=23.9, lng=121.6)
    say("U-bot2", "登記物資")
    assert "/f/res?t=" in str(line_outbox.sent[-1][2].contents.to_dict())
    say("U-bot", "我要當志工")
    assert "/f/apply?t=" in str(line_outbox.sent[-1][2].contents.to_dict())
