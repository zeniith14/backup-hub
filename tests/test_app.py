import time

import pytest

from app.config import ConfigError, parse_config
from app.parse import parse_line
from app.runner import build_cmd
from conftest import basic


def wait_idle(client, timeout=15):
    t0 = time.time()
    while time.time() - t0 < timeout:
        if not client.get("/api/status").json()["manager"]["running"]:
            return
        time.sleep(0.1)
    raise AssertionError("il backup non è terminato in tempo")


# ---- parsing ---------------------------------------------------------------
def test_parse_stats_line():
    kind, d = parse_line('{"level":"info","msg":"x","stats":{"bytes":10,"totalBytes":100,"speed":5.5,"eta":null,"transfers":1,"totalTransfers":2,"checks":3,"totalChecks":4,"errors":0}}')
    assert kind == "stats"
    assert d["bytes"] == 10 and d["total_bytes"] == 100 and d["eta"] is None


def test_parse_log_and_raw():
    assert parse_line('{"level":"error","msg":"boom","object":"a/b"}') == ("log", {"level": "error", "msg": "boom", "object": "a/b"})
    assert parse_line("panic: something")[0] == "raw"
    assert parse_line("{broken json")[0] == "raw"


# ---- config ----------------------------------------------------------------
def test_config_validation():
    base = {"jobs": [{"name": "a", "source": "x", "dest": "y"}]}
    assert parse_config(base).jobs[0].mode == "sync"
    with pytest.raises(ConfigError):
        parse_config({"jobs": []})
    with pytest.raises(ConfigError):
        parse_config({**base, "schedule": "not a cron"})
    with pytest.raises(ConfigError):
        parse_config({"jobs": [{"name": "Bad Name", "source": "x", "dest": "y"}]})
    with pytest.raises(ConfigError):
        parse_config({"jobs": [{"name": "a", "source": "x", "dest": "y", "mode": "delete-all"}]})


def test_build_cmd(cfg):
    cmd = build_cmd(cfg, cfg.job("immich"), dry_run=True)
    assert cmd[1:4] == ["sync", "vm101:/a", "gdrive-crypt:immich"]
    assert "--use-json-log" in cmd and "--dry-run" in cmd
    assert cmd[cmd.index("--exclude") + 1] == "thumbs/**"
    assert cmd[cmd.index("--max-delete") + 1] == "100"
    assert "--dry-run" not in build_cmd(cfg, cfg.job("immich"), dry_run=False)


# ---- auth ------------------------------------------------------------------
def test_auth_required(cfg):
    from fastapi.testclient import TestClient

    from app.main import create_app

    app = create_app(cfg, start_scheduler=False)
    with TestClient(app) as c:
        assert c.get("/api/status").status_code == 401
        assert c.get("/").status_code == 401
        assert c.get("/api/status", headers=basic("sbagliata")).status_code == 401
        assert c.get("/api/status", headers=basic()).status_code == 200


# ---- esecuzione ------------------------------------------------------------
def test_full_run_ok(client):
    assert client.post("/api/run", json={}).status_code == 200
    wait_idle(client)
    runs = client.get("/api/runs").json()
    assert {r["job"] for r in runs} == {"immich", "opencloud"}
    assert all(r["status"] == "ok" for r in runs)
    r = runs[0]
    assert r["bytes"] == 5 * 1024**3 and r["transfers"] == 1200 and r["trigger"] == "manuale"
    st = client.get("/api/status").json()
    assert st["last_run"]["status"] == "ok"
    assert all(j["estimate"] is not None for j in st["jobs"])
    assert st["next_estimate"] is not None
    log = client.get(f"/api/runs/{r['id']}/log").json()
    assert any("FINE status=ok" in line for line in log["lines"])


def test_failure_is_reported(client, monkeypatch):
    monkeypatch.setenv("FAKE_MODE", "fail")
    client.post("/api/run", json={"jobs": ["immich"]})
    wait_idle(client)
    r = client.get("/api/runs").json()[0]
    assert r["status"] == "failed" and r["rc"] == 1
    assert "Rate Limit" in r["last_error"] or "Attempt 3/3" in r["last_error"]


def test_dry_run_does_not_count_for_estimate(client):
    client.post("/api/run", json={"jobs": ["immich"], "dry_run": True})
    wait_idle(client)
    assert client.get("/api/runs").json()[0]["dry_run"] is True
    job = next(j for j in client.get("/api/status").json()["jobs"] if j["name"] == "immich")
    assert job["estimate"] is None and job["last_run"] is None


def test_busy_and_cancel(client, monkeypatch):
    monkeypatch.setenv("FAKE_MODE", "slow")
    assert client.post("/api/run", json={"jobs": ["immich"]}).status_code == 200
    time.sleep(0.6)
    assert client.post("/api/run", json={}).status_code == 409
    snap = client.get("/api/status").json()["manager"]
    assert snap["running"] and snap["current"]["job"] == "immich"
    assert snap["current"]["percent"] is not None and snap["current"]["progress"]["transferring"]
    assert client.post("/api/cancel").status_code == 200
    wait_idle(client)
    assert client.get("/api/runs").json()[0]["status"] == "cancelled"
    assert client.post("/api/cancel").status_code == 409


def test_unknown_job(client):
    assert client.post("/api/run", json={"jobs": ["nope"]}).status_code == 400


def test_missing_rclone_binary(cfg):
    from fastapi.testclient import TestClient

    from app.main import create_app

    cfg.rclone_binary = "/non/esiste/rclone"
    with TestClient(create_app(cfg, start_scheduler=False), headers=basic()) as c:
        c.post("/api/run", json={"jobs": ["immich"]})
        wait_idle(c)
        r = c.get("/api/runs").json()[0]
        assert r["status"] == "failed" and "non trovato" in r["last_error"]


def test_interrupted_runs_marked_on_startup(cfg):
    from app.db import DB

    db = DB(cfg.data_dir / "backup-hub.db")
    rid = db.create_run("immich", "manuale", False, "x")
    DB(cfg.data_dir / "backup-hub.db")  # nuovo avvio
    assert db.get_run(rid)["status"] == "interrupted"


# ---- schedule --------------------------------------------------------------
def test_schedule_update_and_preview(client):
    assert client.get("/api/schedule/preview", params={"cron": "0 */6 * * *"}).json()["next"].__len__() == 3
    assert client.get("/api/schedule/preview", params={"cron": "boh"}).status_code == 400
    r = client.post("/api/schedule", json={"cron": "15 4 * * 0", "enabled": True}).json()
    assert r["cron"] == "15 4 * * 0" and r["next_run"]
    r = client.post("/api/schedule", json={"cron": "15 4 * * 0", "enabled": False}).json()
    assert r["enabled"] is False and r["next_run"] is None
    assert client.post("/api/schedule", json={"cron": "x y"}).status_code == 400


def test_scheduler_fires_when_due(client):
    from datetime import datetime, timedelta

    sch = client.app_.state.scheduler
    sch._recompute()
    sch._next = datetime.now(sch.tz) - timedelta(seconds=1)
    sch.run_once()
    wait_idle(client)
    assert {r["trigger"] for r in client.get("/api/runs").json()} == {"automatico"}
    assert sch.info()["next_run"] > datetime.now(sch.tz).isoformat()


def test_log_endpoint_rejects_unknown(client):
    assert client.get("/api/runs/9999/log").status_code == 404
