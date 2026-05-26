# -*- coding: utf-8 -*-
import streamlit as st
import requests
import folium
from streamlit_folium import st_folium
import pandas as pd
from datetime import datetime

BASE = "http://localhost:8000"

st.set_page_config(
    page_title="鄰里守望平台",
    page_icon="🏘️",
    layout="wide",
)

# ── CSS ────────────────────────────────────────────────────────────
st.markdown("""
<style>
.metric-card {
    background: #f0f2f6; border-radius: 10px;
    padding: 16px; text-align: center;
}
.status-ok       { color: #27ae60; font-weight: bold; }
.status-no_resp  { color: #e74c3c; font-weight: bold; }
.status-pending  { color: #f39c12; font-weight: bold; }
.status-not_sent { color: #95a5a6; }
</style>
""", unsafe_allow_html=True)

# ── 工具函數 ───────────────────────────────────────────────────────
def api(path, method="GET", **kwargs):
    try:
        if method == "POST":
            r = requests.post(f"{BASE}{path}", params=kwargs, timeout=5)
        else:
            r = requests.get(f"{BASE}{path}", timeout=5)
        return r.json()
    except Exception as e:
        return {"error": str(e)}

STATUS_LABEL = {
    "ok":            ("✅ 已回應",   "status-ok"),
    "no_response":   ("🚨 未回應",   "status-no_resp"),
    "pending":       ("⏳ 等待中",   "status-pending"),
    "help_needed":   ("🆘 需要幫忙", "status-no_resp"),
    "confirmed_safe":("✅ 確認安全", "status-ok"),
    "not_sent":      ("－ 未發送",   "status-not_sent"),
}

RESOURCE_ICON = {
    "water":     "💧", "food":      "🍱",
    "first_aid": "🩹", "shelter":   "🏠",
    "vehicle":   "🚗", "tool":      "🔧",
    "other":     "📦",
}

# ── Header ─────────────────────────────────────────────────────────
summary = api("/api/dashboard/summary")
mode    = summary.get("mode", "normal")
is_emergency = mode == "emergency"

col_title, col_mode = st.columns([4, 1])
with col_title:
    st.title("🏘️ 鄰里守望平台")
    st.caption(f"更新時間：{datetime.now().strftime('%Y-%m-%d %H:%M')}")
with col_mode:
    st.markdown("<br>", unsafe_allow_html=True)
    if is_emergency:
        st.error("🚨 緊急模式")
        if st.button("切換回日常模式"):
            api("/api/dashboard/mode", "POST", mode="normal")
            st.rerun()
    else:
        st.success("🟢 日常模式")
        if st.button("宣告緊急模式"):
            api("/api/dashboard/mode", "POST", mode="emergency")
            st.rerun()

st.divider()

# ── 今日數據 ────────────────────────────────────────────────────────
checkin_summary = summary.get("checkin_summary", {})
c1, c2, c3, c4, c5 = st.columns(5)
c1.metric("✅ 已回應",  checkin_summary.get("ok", 0))
c2.metric("🚨 未回應",  checkin_summary.get("no_response", 0))
c3.metric("⏳ 等待中",  checkin_summary.get("pending", 0))
c4.metric("🔔 未解警報", summary.get("active_alerts", 0))
c5.metric("📦 可用物資", summary.get("available_resources", 0))

st.divider()

# ── 主要內容（Tab）─────────────────────────────────────────────────
tab1, tab2, tab3 = st.tabs(["👴 長者狀態", "📦 社區物資地圖", "🚨 警報記錄"])

# ══════════════════════════════════════════════
# Tab 1：長者狀態
# ══════════════════════════════════════════════
with tab1:
    elderly_list = api("/api/dashboard/elderly")

    if "error" in elderly_list if isinstance(elderly_list, dict) else False:
        st.error("無法連接 API，請確認 server 正在運行")
    elif not elderly_list:
        st.info("目前沒有長者資料，請先執行 seed.py")
    else:
        rows = []
        for e in elderly_list:
            label, css = STATUS_LABEL.get(e.get("today_status", "not_sent"),
                                          ("－", "status-not_sent"))
            rows.append({
                "姓名":     e["name"],
                "電話":     e.get("phone", "—"),
                "地址":     e.get("address", "—"),
                "今日狀態": label,
                "回應時間": e.get("responded_at", "—") or "—",
            })

        df = pd.DataFrame(rows)

        # 搜尋
        search = st.text_input("搜尋長者姓名或地址", "")
        if search:
            df = df[df["姓名"].str.contains(search) | df["地址"].str.contains(search)]

        # 顏色標示
        def color_status(val):
            colors = {
                "✅ 已回應": "color: #27ae60",
                "🚨 未回應": "color: #e74c3c; font-weight: bold",
                "⏳ 等待中": "color: #f39c12",
                "🆘 需要幫忙": "color: #e74c3c; font-weight: bold",
            }
            return colors.get(val, "")

        styled = df.style.applymap(color_status, subset=["今日狀態"])
        st.dataframe(styled, use_container_width=True, hide_index=True)

        st.caption(f"共 {len(rows)} 位長者")

# ══════════════════════════════════════════════
# Tab 2：社區物資地圖
# ══════════════════════════════════════════════
with tab2:
    resources = api("/api/resources/", method="GET")
    elderly_list2 = api("/api/dashboard/elderly")

    col_map, col_list = st.columns([3, 2])

    with col_map:
        m = folium.Map(location=[24.125, 120.678], zoom_start=14)

        # 長者位置（藍色）
        if isinstance(elderly_list2, list):
            for e in elderly_list2:
                if e.get("lat") and e.get("lng"):
                    status = e.get("today_status", "not_sent")
                    color = {"ok": "green", "no_response": "red",
                             "pending": "orange"}.get(status, "gray")
                    folium.CircleMarker(
                        location=[e["lat"], e["lng"]],
                        radius=10, color=color, fill=True, fill_opacity=0.8,
                        popup=folium.Popup(
                            f"<b>{e['name']}</b><br>{STATUS_LABEL.get(status,('',''))[0]}",
                            max_width=150
                        ),
                        tooltip=e["name"],
                    ).add_to(m)

        # 物資位置（綠色）
        if isinstance(resources, list):
            for r in resources:
                if r.get("lat") and r.get("lng"):
                    icon_char = RESOURCE_ICON.get(r["resource_type"], "📦")
                    folium.Marker(
                        location=[r["lat"], r["lng"]],
                        popup=folium.Popup(
                            f"<b>{icon_char} {r['name']}</b><br>"
                            f"數量：{r.get('quantity','—')}<br>"
                            f"登記人：{r.get('owner','—')}",
                            max_width=180
                        ),
                        tooltip=f"{icon_char} {r['name']}",
                        icon=folium.Icon(color="green", icon="info-sign"),
                    ).add_to(m)

        # 圖例
        legend = """
        <div style="position:fixed;bottom:30px;left:30px;z-index:1000;
             background:white;padding:10px;border-radius:8px;
             border:1px solid #ccc;font-size:12px;">
        <b>圖例</b><br>
        🟢 已回應　🔴 未回應　🟠 等待中<br>
        📍 綠色標記 = 社區物資
        </div>
        """
        m.get_root().html.add_child(folium.Element(legend))
        st_folium(m, width=600, height=450)

    with col_list:
        st.subheader("物資清單")
        if isinstance(resources, list) and resources:
            for r in resources:
                icon = RESOURCE_ICON.get(r["resource_type"], "📦")
                avail = "✅ 可用" if r["is_available"] else "❌ 不可用"
                with st.expander(f"{icon} {r['name']} — {avail}"):
                    st.write(f"**數量**：{r.get('quantity', '—')}")
                    st.write(f"**地址**：{r.get('address', '—')}")
                    st.write(f"**登記人**：{r.get('owner', '—')}")
                    if r.get("note"):
                        st.write(f"**備注**：{r['note']}")
        else:
            st.info("目前沒有物資資料")

# ══════════════════════════════════════════════
# Tab 3：警報記錄
# ══════════════════════════════════════════════
with tab3:
    alerts = api("/api/dashboard/alerts")

    if isinstance(alerts, list) and alerts:
        for a in alerts:
            alert_type = a.get("alert_type", "")
            if "3h" in alert_type:
                st.error(f"🚨 **{a['elderly']}** — 超過 3 小時未回應 （{a['created_at'][:16]}）")
            elif "1h" in alert_type:
                st.warning(f"⚠️ **{a['elderly']}** — 超過 1 小時未回應 （{a['created_at'][:16]}）")
            else:
                st.info(f"ℹ️ **{a['elderly']}** — {alert_type} （{a['created_at'][:16]}）")
    else:
        st.success("目前沒有未解決的警報 👍")

# ── 側邊欄：快速操作 ───────────────────────────────────────────────
with st.sidebar:
    st.header("快速操作")
    if st.button("重新整理"):
        st.rerun()

    st.divider()
    st.subheader("緊急需求（災時）")
    need_type = st.selectbox("需求類型",
        ["water", "food", "first_aid", "shelter", "vehicle", "other"])
    need_desc = st.text_input("描述")
    need_addr = st.text_input("地點")
    if st.button("提交需求"):
        st.warning("需求提交功能需要登入（LINE 綁定）")

    st.divider()
    st.caption("鄰里守望平台 MVP v0.1")
    st.caption("平時守護長者，災時守護社區")
