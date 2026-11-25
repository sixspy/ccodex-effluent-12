"""准排放裁定放行（污泥禁准口径：无污泥排程禁准排）。

正确性口径（污泥仓位计划与准排写口同源，不得混用）：
- 甲路径：污泥排程无匹配消化仓行（空桩）时，准排写口原子拒收，正式册零增，
  污泥禁准裁决写入说明并当日封死。
- 乙路径：准排先登记影子意图（正式区零改），升正须仓位计划背书，
  否则污泥意图作废并记污泥禁准偏航、拒写。
"""
from __future__ import annotations

from datetime import date

from fastapi import APIRouter, HTTPException

from auth import AuthHeader, require_user, require_writer
from db import connect, now_iso, write_audit
from locks import release, require_lock
from models import ReleaseIn
from validators import validate_sample_releasable

router = APIRouter(tags=["release"])

_SLUDGE_PLAN_SQL = """SELECT j.id, j.digester_bay FROM sludge_plan_jobs j
       JOIN outfalls o ON o.plant_id=j.plant_id
       JOIN sample_batches s ON s.outfall_id=o.id
       WHERE s.id=? AND j.status='排队' LIMIT 1"""


def _sludge_plan_row(conn, sample_id: int):
    """同源联查：采样批次所属处理厂是否已有污泥消化仓排程行（仓位计划）。"""
    return conn.execute(_SLUDGE_PLAN_SQL, (sample_id,)).fetchone()


def _sludge_denial(conn, user: str, sample_code: str, sample_id: int) -> None:
    """污泥禁准裁决写入说明并当日封死（随主事务提交，拒写后不得丢失）。"""
    write_audit(
        conn,
        user,
        "污泥禁准裁决",
        sample_code,
        {
            "ruling": "禁准",
            "reason": "污泥排程无匹配消化仓行（空桩）",
            "caliber": "无污泥排程禁准排",
            "sealed_on": date.today().isoformat(),
            "sample_id": sample_id,
        },
    )


def _load_sample(conn, sample_id: int):
    sample = conn.execute(
        "SELECT * FROM sample_batches WHERE id=?", (sample_id,)
    ).fetchone()
    if not sample:
        raise HTTPException(404, "采样批次不存在")
    return sample


def _check_releasable(conn, sample) -> None:
    validate_sample_releasable(sample["status"])
    open_hold = conn.execute(
        "SELECT id FROM exceed_holds WHERE sample_id=? AND status='开放'",
        (sample["id"],),
    ).fetchone()
    if open_hold:
        raise HTTPException(400, "仍有开放超标挂起，禁止准排")
    existing = conn.execute(
        "SELECT id FROM discharge_releases WHERE sample_id=? AND status='已准排'",
        (sample["id"],),
    ).fetchone()
    if existing:
        raise HTTPException(400, "已准排，勿重复")


@router.get("/api/releases")
def list_releases(authorization: AuthHeader = None):
    require_user(authorization)
    with connect() as conn:
        rows = conn.execute(
            """SELECT r.*, s.sample_code, o.outfall_code,
                      EXISTS(SELECT 1 FROM sludge_plan_jobs j
                             WHERE j.plant_id=o.plant_id AND j.status='排队') AS sludge_backed
               FROM discharge_releases r
               JOIN sample_batches s ON s.id=r.sample_id
               JOIN outfalls o ON o.id=r.outfall_id
               ORDER BY r.id DESC"""
        ).fetchall()
        items = []
        for r in rows:
            d = dict(r)
            d["sludge_backed"] = bool(d["sludge_backed"])
            items.append(d)
        return {"items": items}


@router.post("/api/releases")
def create_release(body: ReleaseIn, authorization: AuthHeader = None):
    """甲路径：正式准排写口。无污泥排程行则原子拒收、正式册零增。"""
    user = require_writer(authorization)
    lock_key = f"sample:{body.sample_id}"
    with connect() as conn:
        require_lock(conn, lock_key, user)
        try:
            sample = _load_sample(conn, body.sample_id)
            _check_releasable(conn, sample)
            if not _sludge_plan_row(conn, body.sample_id):
                _sludge_denial(conn, user, sample["sample_code"], body.sample_id)
                conn.commit()
                raise HTTPException(
                    400, "污泥禁准：污泥排程无匹配消化仓行（空桩），准排写口原子拒收"
                )
            ref = f"RL-{sample['sample_code']}"
            conn.execute(
                """INSERT INTO discharge_releases(sample_id,outfall_id,release_ref,status,officer,released_at)
                   VALUES (?,?,?,?,?,?)""",
                (
                    body.sample_id,
                    sample["outfall_id"],
                    ref,
                    "已准排",
                    body.officer,
                    now_iso(),
                ),
            )
            conn.execute(
                "UPDATE sample_batches SET status=? WHERE id=?",
                ("已准排", body.sample_id),
            )
            write_audit(
                conn,
                user,
                "release",
                sample["sample_code"],
                {"release_ref": ref, "officer": body.officer},
            )
            conn.commit()
            return {"release_ref": ref, "status": "已准排"}
        finally:
            release(conn, lock_key, user)
            conn.commit()


@router.post("/api/release-intents")
def create_release_intent(body: ReleaseIn, authorization: AuthHeader = None):
    """乙路径：先登记影子意图。正式区零改（批态不动、正式册零增）。"""
    user = require_writer(authorization)
    lock_key = f"sample:{body.sample_id}"
    with connect() as conn:
        require_lock(conn, lock_key, user)
        try:
            sample = _load_sample(conn, body.sample_id)
            _check_releasable(conn, sample)
            active = conn.execute(
                """SELECT id FROM discharge_releases
                   WHERE sample_id=? AND status='影子意图'""",
                (body.sample_id,),
            ).fetchone()
            if active:
                raise HTTPException(400, "已有在册影子意图，勿重复登记")
            ref = f"RI-{sample['sample_code']}"
            cur = conn.execute(
                """INSERT INTO discharge_releases(sample_id,outfall_id,release_ref,status,officer,released_at)
                   VALUES (?,?,?,?,?,?)""",
                (
                    body.sample_id,
                    sample["outfall_id"],
                    ref,
                    "影子意图",
                    body.officer,
                    now_iso(),
                ),
            )
            write_audit(
                conn,
                user,
                "release_intent",
                sample["sample_code"],
                {"intent_ref": ref, "officer": body.officer, "note": "影子意图，正式区零改"},
            )
            conn.commit()
            return {"id": cur.lastrowid, "intent_ref": ref, "status": "影子意图"}
        finally:
            release(conn, lock_key, user)
            conn.commit()


@router.post("/api/release-intents/{intent_id}/promote")
def promote_release_intent(intent_id: int, authorization: AuthHeader = None):
    """乙路径升正：有仓位计划背书则升正为正式准排，否则污泥意图作废并记偏航。"""
    user = require_writer(authorization)
    with connect() as conn:
        intent = conn.execute(
            "SELECT * FROM discharge_releases WHERE id=?", (intent_id,)
        ).fetchone()
        if not intent or intent["status"] != "影子意图":
            raise HTTPException(404, "影子意图不存在或已了结")
        sample = _load_sample(conn, intent["sample_id"])
        lock_key = f"sample:{intent['sample_id']}"
        require_lock(conn, lock_key, user)
        try:
            existing = conn.execute(
                "SELECT id FROM discharge_releases WHERE sample_id=? AND status='已准排'",
                (intent["sample_id"],),
            ).fetchone()
            if existing:
                raise HTTPException(400, "已准排，勿重复")
            if _sludge_plan_row(conn, intent["sample_id"]):
                conn.execute(
                    """UPDATE discharge_releases SET status=?, released_at=? WHERE id=?""",
                    ("已准排", now_iso(), intent_id),
                )
                conn.execute(
                    "UPDATE sample_batches SET status=? WHERE id=?",
                    ("已准排", intent["sample_id"]),
                )
                write_audit(
                    conn,
                    user,
                    "release_promote",
                    sample["sample_code"],
                    {"intent_ref": intent["release_ref"], "ruling": "升正"},
                )
                conn.commit()
                return {"intent_ref": intent["release_ref"], "status": "已准排"}
            conn.execute(
                "UPDATE discharge_releases SET status=? WHERE id=?",
                ("已作废", intent_id),
            )
            write_audit(
                conn,
                user,
                "污泥禁准偏航",
                sample["sample_code"],
                {
                    "ruling": "偏航",
                    "reason": "无仓位计划背书试图升正，污泥意图作废",
                    "caliber": "无污泥排程禁准排",
                    "sealed_on": date.today().isoformat(),
                    "intent_ref": intent["release_ref"],
                },
            )
            conn.commit()
            raise HTTPException(
                400, "污泥禁准偏航：无仓位计划背书，升正拒写，污泥意图作废"
            )
        finally:
            release(conn, lock_key, user)
            conn.commit()
