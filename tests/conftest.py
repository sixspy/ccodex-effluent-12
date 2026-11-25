"""effluent-12 污泥禁准事故夹具。

事故原型：污泥排程空桩尚无消化仓行 SD-L12（sludge_plan_jobs 无“排队”行），
准排写口却已出正式准排 SM-L12（discharge_releases 已准排），
空桩页空而对账已准排 —— 无污泥排程却能准排。
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app import app
from db import connect, init_db, now_iso


def auth(c: TestClient, user="admin", password="admin123") -> dict:
    tok = c.post(
        "/api/login", json={"username": user, "password": password}
    ).json()["token"]
    return {"Authorization": "Bearer " + tok}


@pytest.fixture
def effluent12(tmp_path, monkeypatch):
    """事故夹具：PL-L12 / OF-L12-A / DP-2026-L12 / SM-L12 已裁定，空桩无 SD-L12。"""
    monkeypatch.setattr("db.DB_PATH", tmp_path / "effluent12.db")
    init_db()
    c = TestClient(app)
    h = auth(c)
    plant = c.post(
        "/api/plants",
        headers=h,
        json={
            "plant_code": "PL-L12",
            "name": "临十二水质净化厂",
            "basin": "临十二流域",
            "design_cap_m3d": 80000,
        },
    ).json()
    outfall = c.post(
        "/api/outfalls",
        headers=h,
        json={
            "outfall_code": "OF-L12-A",
            "plant_id": plant["id"],
            "river_reach": "临十二渠",
            "cod_limit": 50,
            "nh3n_limit": 5,
            "tp_limit": 0.5,
        },
    ).json()
    c.post(
        "/api/permits",
        headers=h,
        json={
            "permit_code": "DP-2026-L12",
            "plant_id": plant["id"],
            "quota_m3d": 20000,
            "cod_cap": 50,
            "nh3n_cap": 5,
            "tp_cap": 0.5,
            "period_label": "2026Q4",
        },
    )
    sample = c.post(
        "/api/samples",
        headers=h,
        json={
            "outfall_id": outfall["id"],
            "permit_ref": "DP-2026-L12",
            "sample_code": "SM-L12",
            "flow_m3h": 1000,
            "metrics": {"cod": 30.0, "nh3n": 2.0, "tp": 0.2},
        },
    ).json()
    assert sample["status"] == "已裁定"
    return SimpleNamespace(
        client=c,
        headers=h,
        plant_id=plant["id"],
        outfall_id=outfall["id"],
        sample_id=sample["id"],
        sample_code="SM-L12",
        permit_code="DP-2026-L12",
    )


def enqueue_sludge_plan(ns, bay="SD-L12"):
    """空桩补消化仓行：enqueue 产生“排队”行（仓位计划口径来源）。"""
    return ns.client.post(
        "/api/stubs/sludge-plan",
        headers=ns.headers,
        json={"plant_id": ns.plant_id, "digester_bay": bay, "mode": "enqueue"},
    )


def seed_dirty_release_without_plan(ns):
    """直接落库事故脏数据：无 SD-L12 却出正式准排 RL-SM-L12（绕过写口的历史事故）。"""
    with connect() as conn:
        conn.execute(
            """INSERT INTO discharge_releases(sample_id,outfall_id,release_ref,status,officer,released_at)
               VALUES (?,?,?,?,?,?)""",
            (ns.sample_id, ns.outfall_id, "RL-SM-L12", "已准排", "事故值班", now_iso()),
        )
        conn.execute(
            "UPDATE sample_batches SET status=? WHERE id=?",
            ("已准排", ns.sample_id),
        )
        conn.commit()


def count_formal_releases(ns, sample_id=None) -> int:
    """正式册（discharge_releases 已准排）行数。"""
    with connect() as conn:
        if sample_id is None:
            row = conn.execute(
                "SELECT COUNT(*) c FROM discharge_releases WHERE status='已准排'"
            ).fetchone()
        else:
            row = conn.execute(
                "SELECT COUNT(*) c FROM discharge_releases WHERE status='已准排' AND sample_id=?",
                (sample_id,),
            ).fetchone()
        return row["c"]
