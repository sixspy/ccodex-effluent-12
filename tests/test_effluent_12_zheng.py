"""effluent-12 污泥禁准 · 争用：同批抢准排须占准排锁，败方只记争用痕不增准排。"""
from __future__ import annotations

import threading

from conftest import auth, effluent12, enqueue_sludge_plan  # noqa: F401
from db import connect


def test_release_lock_loser_trace_no_release_increase(effluent12):
    """争用失败测：两路会话抢同批准排，败方 409 只记争用痕、正式册零增。"""
    ns = effluent12
    enqueue_sludge_plan(ns)  # 补 SD-L12，隔离准排锁行为
    h_writer = auth(ns.client, "writer", "writer123")
    lock_key = f"sample:{ns.sample_id}"
    held = ns.client.post(
        f"/api/locks/acquire?lock_key={lock_key}", headers=ns.headers
    )
    assert held.status_code == 200
    resp = ns.client.post(
        "/api/releases",
        headers=h_writer,
        json={"sample_id": ns.sample_id, "officer": "writer"},
    )
    assert resp.status_code == 409, "败方须撞准排锁"
    locks = ns.client.get("/api/locks", headers=ns.headers).json()
    traces = [
        c for c in locks["contention"]
        if c["lock_key"] == lock_key and c["actor"] == "writer"
    ]
    assert traces, "败方须留争用痕（contention_log 不得随回滚丢失）"
    releases = ns.client.get("/api/releases", headers=h_writer).json()["items"]
    mine = [r for r in releases if r["sample_id"] == ns.sample_id]
    assert mine == [], "败方零行：不增准排"


def test_concurrent_dual_release_single_winner(effluent12):
    """并发双请求打同批准排：恰一胜方，败方零行。"""
    ns = effluent12
    enqueue_sludge_plan(ns)
    h_admin = ns.headers
    h_writer = auth(ns.client, "writer", "writer123")
    from fastapi.testclient import TestClient

    from app import app

    barrier = threading.Barrier(2)
    results: list[tuple[str, int, dict]] = []

    def hit(tag: str, headers: dict) -> None:
        c = TestClient(app)
        barrier.wait(timeout=10)
        r = c.post(
            "/api/releases",
            headers=headers,
            json={"sample_id": ns.sample_id, "officer": tag},
        )
        results.append((tag, r.status_code, r.json() if r.content else {}))

    t1 = threading.Thread(target=hit, args=("admin", h_admin))
    t2 = threading.Thread(target=hit, args=("writer", h_writer))
    t1.start()
    t2.start()
    t1.join(timeout=30)
    t2.join(timeout=30)
    assert len(results) == 2, "双会话均须返回"
    codes = [code for _, code, _ in results]
    assert codes.count(200) == 1 and any(c in (400, 409) for c in codes), (
        f"须恰一胜方一败方，实得 {[(t, c) for t, c, _ in results]}"
    )
    with connect() as conn:
        formal = conn.execute(
            "SELECT COUNT(*) c FROM discharge_releases WHERE sample_id=? AND status='已准排'",
            (ns.sample_id,),
        ).fetchone()["c"]
        contention = conn.execute(
            "SELECT COUNT(*) c FROM contention_log WHERE lock_key=?",
            (f"sample:{ns.sample_id}",),
        ).fetchone()["c"]
    assert formal == 1, "同批正式准排恰一行（败方零行）"
    if 409 in codes:
        assert contention >= 1, "409 败方须留争用痕"
