"""排放批争用锁。"""
from __future__ import annotations

import sqlite3

from fastapi import APIRouter, HTTPException

from auth import AuthHeader, require_user, require_writer
from db import connect, now_iso, write_audit

router = APIRouter(tags=["locks"])


def _record_contention(lock_key: str, actor: str, holder: str) -> None:
    """争用痕独立落库：调用方事务回滚（如 409 拒写）也不得带走争用痕。"""
    with connect() as conn:
        conn.execute(
            """INSERT INTO contention_log(lock_key,actor,action,detail,created_at)
               VALUES (?,?,?,?,?)""",
            (lock_key, actor, "争用失败", f"持有人={holder}", now_iso()),
        )
        conn.commit()


def require_lock(conn, lock_key: str, holder: str) -> None:
    row = conn.execute(
        "SELECT holder FROM effluent_locks WHERE lock_key=?", (lock_key,)
    ).fetchone()
    if row and row["holder"] != holder:
        _record_contention(lock_key, holder, row["holder"])
        raise HTTPException(409, f"锁被占用：{lock_key} 持有人 {row['holder']}")
    if not row:
        try:
            conn.execute(
                "INSERT INTO effluent_locks(lock_key,holder,acquired_at) VALUES (?,?,?)",
                (lock_key, holder, now_iso()),
            )
        except sqlite3.IntegrityError:
            # 并发会话在本会话 SELECT 与 INSERT 之间抢先占锁（主键冲突即败方）
            other = conn.execute(
                "SELECT holder FROM effluent_locks WHERE lock_key=?", (lock_key,)
            ).fetchone()
            holder_now = other["holder"] if other else "未知"
            _record_contention(lock_key, holder, holder_now)
            raise HTTPException(409, f"锁被占用：{lock_key} 持有人 {holder_now}")


def release(conn, lock_key: str, holder: str) -> None:
    conn.execute(
        "DELETE FROM effluent_locks WHERE lock_key=? AND holder=?",
        (lock_key, holder),
    )


@router.get("/api/locks")
def list_locks(authorization: AuthHeader = None):
    require_user(authorization)
    with connect() as conn:
        locks = [dict(r) for r in conn.execute("SELECT * FROM effluent_locks").fetchall()]
        logs = [
            dict(r)
            for r in conn.execute(
                "SELECT * FROM contention_log ORDER BY id DESC LIMIT 50"
            ).fetchall()
        ]
        return {"locks": locks, "contention": logs}


@router.post("/api/locks/acquire")
def acquire(lock_key: str, authorization: AuthHeader = None):
    user = require_writer(authorization)
    with connect() as conn:
        require_lock(conn, lock_key, user)
        write_audit(conn, user, "lock_acquire", lock_key, {})
        conn.commit()
        return {"ok": True, "lock_key": lock_key, "holder": user}


@router.post("/api/locks/release")
def release_api(lock_key: str, authorization: AuthHeader = None):
    user = require_writer(authorization)
    with connect() as conn:
        release(conn, lock_key, user)
        write_audit(conn, user, "lock_release", lock_key, {})
        conn.commit()
        return {"ok": True}
