"""effluent-12 污泥禁准 · 乙路径：影子意图未升正，对账不得标已准排。

口径：准排可先登记影子意图（正式区零改），升正须污泥排程背书，
否则污泥意图作废并记污泥禁准偏航；对账只认有仓位计划背书的正式准排。
"""
from __future__ import annotations

from conftest import (  # noqa: F401
    auth,
    effluent12,
    enqueue_sludge_plan,
    seed_dirty_release_without_plan,
)


def test_reconcile_not_mark_unpromoted_intent_as_released(effluent12):
    """失败测乙：事故脏数据（无 SD-L12 的正式准排）对账不得标已准排。"""
    ns = effluent12
    seed_dirty_release_without_plan(ns)
    proof = ns.client.get("/api/proof/sample/SM-L12", headers=ns.headers).json()
    assert proof["released"] is False, "无仓位计划背书的准排只是未升正意图，对账不得标已准排"
    assert proof["sludge_backed"] is False
    recon = ns.client.get("/api/reconcile/DP-2026-L12", headers=ns.headers).json()
    sample = next(s for s in recon["samples"] if s["sample_code"] == "SM-L12")
    assert sample["released"] is False, "对账册不得把未升正意图计入已准排"
    assert sample["sludge_backed"] is False
    assert recon["released_count"] == 0


def test_shadow_intent_register_keeps_formal_area_untouched(effluent12):
    """乙路径登记：影子意图落册，正式区零改（采样批态不动、正式册零增）。"""
    ns = effluent12
    resp = ns.client.post(
        "/api/release-intents",
        headers=ns.headers,
        json={"sample_id": ns.sample_id, "officer": "值班员"},
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "影子意图"
    proof = ns.client.get("/api/proof/sample/SM-L12", headers=ns.headers).json()
    assert proof["released"] is False, "影子意图不得标已准排"
    assert proof["status"] == "已裁定", "正式区零改：批态不动"
    releases = ns.client.get("/api/releases", headers=ns.headers).json()["items"]
    formal = [r for r in releases if r["sample_id"] == ns.sample_id and r["status"] == "已准排"]
    assert formal == [], "正式册零增"


def test_promote_without_plan_voids_intent_and_records_deviation(effluent12):
    """未升正即促正式区：污泥意图作废，偏离说明记污泥禁准偏航并拒写。"""
    ns = effluent12
    intent = ns.client.post(
        "/api/release-intents",
        headers=ns.headers,
        json={"sample_id": ns.sample_id, "officer": "值班员"},
    ).json()
    resp = ns.client.post(
        f"/api/release-intents/{intent['id']}/promote",
        headers=ns.headers,
    )
    assert resp.status_code == 400, "无仓位计划背书，升正须拒写"
    releases = ns.client.get("/api/releases", headers=ns.headers).json()["items"]
    mine = [r for r in releases if r["sample_id"] == ns.sample_id]
    assert [r for r in mine if r["status"] == "已作废"], "污泥意图须作废"
    assert not [r for r in mine if r["status"] == "已准排"], "正式册零增"
    audit = ns.client.get("/api/audit", headers=ns.headers).json()["items"]
    dev = [a for a in audit if a["action"] == "污泥禁准偏航" and a["target"] == ns.sample_code]
    assert dev, "偏离说明须记污泥禁准偏航"


def test_promote_with_plan_turns_intent_formal(effluent12):
    """乙路径升正：空桩补 SD-L12 后影子意图升正为正式准排。"""
    ns = effluent12
    intent = ns.client.post(
        "/api/release-intents",
        headers=ns.headers,
        json={"sample_id": ns.sample_id, "officer": "值班员"},
    ).json()
    enqueue_sludge_plan(ns)
    resp = ns.client.post(
        f"/api/release-intents/{intent['id']}/promote",
        headers=ns.headers,
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "已准排"
    proof = ns.client.get("/api/proof/sample/SM-L12", headers=ns.headers).json()
    assert proof["released"] is True, "升正且有仓位计划背书，对账方可标已准排"
    assert proof["sludge_backed"] is True
