"""空桩：污泥消化仓排程（题面可点到接口，默认 dry_run）。

口径：只有 enqueue 产生的“排队”行计入仓位计划（污泥禁准同源），
dry_run 演练行不计入；五页同源读本表。
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException

from auth import AuthHeader, require_user, require_writer
from db import connect, now_iso, write_audit
from models import SludgePlanIn

router = APIRouter(tags=["sludge_plan_stub"])


def _with_caliber(rows) -> list[dict]:
    items = []
    for r in rows:
        d = dict(r)
        d["counts_as_plan"] = d["status"] == "排队"
        items.append(d)
    return items


@router.get("/api/stubs/sludge-plan")
def list_jobs(authorization: AuthHeader = None):
    require_user(authorization)
    with connect() as conn:
        rows = conn.execute(
            "SELECT * FROM sludge_plan_jobs ORDER BY id DESC"
        ).fetchall()
        items = _with_caliber(rows)
        scheduled = sum(1 for i in items if i["counts_as_plan"])
        return {
            "items": items,
            "stub": True,
            "ready": False,
            "scheduled_count": scheduled,
        }


@router.post("/api/stubs/sludge-plan")
def enqueue(body: SludgePlanIn, authorization: AuthHeader = None):
    user = require_writer(authorization)
    if body.mode not in ("dry_run", "enqueue"):
        raise HTTPException(400, "仅支持 dry_run / enqueue")
    with connect() as conn:
        if body.plant_id is not None and not conn.execute(
            "SELECT id FROM plants WHERE id=?", (body.plant_id,)
        ).fetchone():
            raise HTTPException(404, "处理厂不存在")
        status = "演练" if body.mode == "dry_run" else "排队"
        cur = conn.execute(
            """INSERT INTO sludge_plan_jobs(plant_id,digester_bay,mode,status,detail,created_at)
               VALUES (?,?,?,?,?,?)""",
            (
                body.plant_id,
                body.digester_bay,
                body.mode,
                status,
                body.detail or "空桩未接通污泥排程",
                now_iso(),
            ),
        )
        write_audit(
            conn,
            user,
            "sludge_plan_stub",
            body.digester_bay,
            body.model_dump(),
        )
        conn.commit()
        return {
            "id": cur.lastrowid,
            "status": status,
            "stub": True,
            "counts_as_plan": status == "排队",
        }
