"""effluent-12 污泥禁准 · 甲路径：无污泥排程，准排写口原子拒收、正式册零增。

口径：污泥仓位计划（sludge_plan_jobs“排队”行）与准排写口同源，
无匹配消化仓行时准排写口原子拒收，污泥禁准裁决写入说明并当日封死。
"""
from __future__ import annotations

from datetime import date

from conftest import auth, count_formal_releases, effluent12, enqueue_sludge_plan  # noqa: F401


def test_no_sludge_plan_release_atomically_rejected(effluent12):
    """失败测甲：无污泥计划仍增正式准排 —— 应原子拒收且正式册零增。"""
    ns = effluent12
    before = count_formal_releases(ns)
    resp = ns.client.post(
        "/api/releases",
        headers=ns.headers,
        json={"sample_id": ns.sample_id, "officer": "值班员"},
    )
    assert resp.status_code == 400, f"空桩无消化仓行，准排写口须原子拒收，实得 {resp.status_code}"
    assert "污泥" in resp.json()["detail"]
    assert count_formal_releases(ns) == before, "正式册须零增"
    rows = ns.client.get("/api/releases", headers=ns.headers).json()["items"]
    assert not [r for r in rows if r["sample_id"] == ns.sample_id], "拒收须原子：不留任何准排行"


def test_sludge_denial_ruling_recorded_and_sealed_same_day(effluent12):
    """污泥禁准裁决写入说明（audit）并当日封死。"""
    ns = effluent12
    ns.client.post(
        "/api/releases",
        headers=ns.headers,
        json={"sample_id": ns.sample_id, "officer": "值班员"},
    )
    audit = ns.client.get("/api/audit", headers=ns.headers).json()["items"]
    rulings = [
        a for a in audit
        if a["action"] == "污泥禁准裁决" and a["target"] == ns.sample_code
    ]
    assert rulings, "污泥禁准裁决须写入说明"
    assert rulings[0]["payload"].find(date.today().isoformat()) >= 0, "裁决须当日封死"


def test_outfall_page_not_substitute_design_for_bin_plan(effluent12):
    """排放口页不得用设计量顶替仓位计划：仓位计划口径唯一起源是污泥排程行。"""
    ns = effluent12
    detail = ns.client.get(f"/api/outfalls/{ns.outfall_id}", headers=ns.headers).json()
    plan = detail.get("sludge_plan")
    assert plan is not None, "排放口须联查污泥排程仓位计划"
    assert plan["bin_plan_ready"] is False, "空桩无消化仓行时仓位计划未就绪"
    assert plan["scheduled_count"] == 0
    assert detail["outfall"]["design_cap_m3d"] > 0, "设计量存在但不得顶替仓位计划"
    enqueue_sludge_plan(ns)
    detail2 = ns.client.get(f"/api/outfalls/{ns.outfall_id}", headers=ns.headers).json()
    assert detail2["sludge_plan"]["bin_plan_ready"] is True
    assert detail2["sludge_plan"]["scheduled_count"] == 1


def test_release_allowed_with_scheduled_sludge_plan(effluent12):
    """正例：空桩补消化仓行 SD-L12（enqueue 排队）后准排放行。"""
    ns = effluent12
    enqueue_sludge_plan(ns)
    resp = ns.client.post(
        "/api/releases",
        headers=ns.headers,
        json={"sample_id": ns.sample_id, "officer": "值班员"},
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "已准排"
    assert count_formal_releases(ns, ns.sample_id) == 1
