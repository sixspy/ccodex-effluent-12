"""ping_sludge_release_lock：并发双会话打同批准排，验证准排锁。

隔离起一台临时 uvicorn（临时目录、临时库），两账号双会话并发 POST 同批 /api/releases：
- 恰一胜方 2xx，败方 4xx（400 已准排勿重复 / 409 锁争用）；
- 败方零行：正式册（discharge_releases 已准排）恰一行；
- 若败方 409，争用痕（contention_log 争用失败）须落册。
退出码 0=口径成立，1=偏航。
"""
from __future__ import annotations

import json
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def _req(method: str, url: str, body: dict | None = None, token: str | None = None):
    data = json.dumps(body).encode("utf-8") if body is not None else None
    r = urllib.request.Request(url, data=data, method=method)
    r.add_header("Content-Type", "application/json")
    if token:
        r.add_header("Authorization", "Bearer " + token)
    try:
        with urllib.request.urlopen(r, timeout=15) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8")
        try:
            return e.code, json.loads(raw)
        except json.JSONDecodeError:
            return e.code, {"raw": raw}


def _login(base: str, user: str, pw: str) -> str:
    code, data = _req("POST", f"{base}/api/login", {"username": user, "password": pw})
    assert code == 200, f"登录失败 {user}: {code} {data}"
    return data["token"]


def main() -> int:
    work = Path(tempfile.mkdtemp(prefix="ping_sludge_lock_"))
    shutil.copytree(ROOT / "backend", work / "backend")
    shutil.copytree(ROOT / "fixtures", work / "fixtures")
    shutil.copytree(ROOT / "frontend", work / "frontend")
    port = _free_port()
    base = f"http://127.0.0.1:{port}"
    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "app:app", "--app-dir", "backend",
         "--host", "127.0.0.1", "--port", str(port)],
        cwd=work, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    try:
        for _ in range(120):
            try:
                code, _ = _req("GET", f"{base}/api/health")
                if code == 200:
                    break
            except OSError:
                time.sleep(0.25)
        else:
            print("FAIL: 临时服务未就绪")
            return 1

        admin = _login(base, "admin", "admin123")
        writer = _login(base, "writer", "writer123")

        # 事故夹具场景（有 SD 排程，隔离准排锁行为）
        _, plant = _req("POST", f"{base}/api/plants", {
            "plant_code": "PL-PING", "name": "并发验证厂", "basin": "验证流域",
            "design_cap_m3d": 80000}, admin)
        _, of = _req("POST", f"{base}/api/outfalls", {
            "outfall_code": "OF-PING-A", "plant_id": plant["id"], "river_reach": "验证渠",
            "cod_limit": 50, "nh3n_limit": 5, "tp_limit": 0.5}, admin)
        _req("POST", f"{base}/api/permits", {
            "permit_code": "DP-PING-01", "plant_id": plant["id"], "quota_m3d": 20000,
            "cod_cap": 50, "nh3n_cap": 5, "tp_cap": 0.5, "period_label": "2026Q4"}, admin)
        _, sm = _req("POST", f"{base}/api/samples", {
            "outfall_id": of["id"], "permit_ref": "DP-PING-01", "sample_code": "SM-PING",
            "flow_m3h": 1000, "metrics": {"cod": 30.0, "nh3n": 2.0, "tp": 0.2}}, admin)
        _, plan = _req("POST", f"{base}/api/stubs/sludge-plan", {
            "plant_id": plant["id"], "digester_bay": "SD-PING", "mode": "enqueue"}, admin)
        assert plan.get("counts_as_plan") is True, f"排程未入账位计划: {plan}"

        barrier = threading.Barrier(2)
        results: list[tuple[str, int, dict]] = []

        def hit(tag: str, token: str) -> None:
            barrier.wait(timeout=10)
            code, data = _req("POST", f"{base}/api/releases",
                              {"sample_id": sm["id"], "officer": tag}, token)
            results.append((tag, code, data))

        t1 = threading.Thread(target=hit, args=("admin", admin))
        t2 = threading.Thread(target=hit, args=("writer", writer))
        t1.start(); t2.start(); t1.join(timeout=30); t2.join(timeout=30)

        codes = sorted((tag, code) for tag, code, _ in results)
        print(f"双会话响应: {[(t, c) for t, c, _ in results]}")
        wins = [r for r in results if r[1] == 200]
        loses = [r for r in results if r[1] in (400, 409)]
        if len(wins) != 1 or len(loses) != 1:
            print(f"FAIL: 须恰一胜一败，实得 {codes}")
            return 1

        _, rel = _req("GET", f"{base}/api/releases", token=admin)
        mine = [r for r in rel["items"] if r["sample_id"] == sm["id"] and r["status"] == "已准排"]
        if len(mine) != 1:
            print(f"FAIL: 正式册须恰一行（败方零行），实得 {len(mine)} 行")
            return 1
        print(f"正式册: 恰一行 {mine[0]['release_ref']}（{mine[0]['officer']} 胜）")

        _, locks = _req("GET", f"{base}/api/locks", token=admin)
        traces = [c for c in locks["contention"] if c["lock_key"] == f"sample:{sm['id']}"]
        loser_code = loses[0][1]
        if loser_code == 409 and not traces:
            print("FAIL: 败方 409 但无争用痕")
            return 1
        print(f"败方 {loses[0][0]}={loser_code}，争用痕 {len(traces)} 条，败方零行 ✓")
        print("PASS: 准排锁口径成立（恰一胜方，败方只记争用痕不增准排）")
        return 0
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
        shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
