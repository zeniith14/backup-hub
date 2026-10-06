import base64
import os
import stat
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import parse_config  # noqa: E402
from app.main import create_app  # noqa: E402

FAKE = Path(__file__).parent / "fake_rclone.py"


def basic(pw="segreta"):
    return {"Authorization": "Basic " + base64.b64encode(f"admin:{pw}".encode()).decode()}


@pytest.fixture()
def cfg(tmp_path):
    FAKE.chmod(FAKE.stat().st_mode | stat.S_IEXEC)
    return parse_config(
        {
            "data_dir": str(tmp_path / "data"),
            "schedule": "30 3 * * *",
            "timezone": "Europe/Rome",
            "auth": {"password": "segreta"},
            "rclone": {"binary": str(FAKE), "flags": ["--tpslimit", "4"]},
            "jobs": [
                {"name": "immich", "label": "Immich", "source": "vm101:/a", "dest": "gdrive-crypt:immich", "excludes": ["thumbs/**"]},
                {"name": "opencloud", "label": "OpenCloud", "source": "vm103:/b", "dest": "gdrive-crypt:opencloud"},
            ],
        }
    )


@pytest.fixture()
def client(cfg):
    from fastapi.testclient import TestClient

    app = create_app(cfg, start_scheduler=False)
    with TestClient(app, headers=basic()) as c:
        c.app_ = app
        yield c


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for k in ("FAKE_MODE", "FAKE_STEPS", "FAKE_DELAY", "BACKUP_HUB_PASSWORD"):
        monkeypatch.delenv(k, raising=False)
