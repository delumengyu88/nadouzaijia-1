"""Puller (ops/w2-sync-puller/sync_puller.py) with a fake COS bucket."""

import datetime
import importlib.util
import json
import os
from pathlib import Path

import pytest

from tests.w2_helpers import build_release

OPS = Path(__file__).resolve().parents[1] / "ops" / "w2-sync-puller"
PREFIX = "w2-index-export"


def load(name, tmp_path, monkeypatch):
    monkeypatch.setenv("W2_LOG_FILE", str(tmp_path / "log" / f"{name}.log"))
    spec = importlib.util.spec_from_file_location(f"ops_{name}", OPS / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def today_ts(days_ago=0):
    when = datetime.datetime.now() - datetime.timedelta(days=days_ago)
    return when.strftime("%Y-%m-%d %H:%M:%S")


def names_rows(count, prefix="片"):
    return [
        (f"{prefix}{i}", f"t{i}", "consensus", "1", "00:00:01", None, "", "done")
        for i in range(count)
    ]


class FakeCos:
    def __init__(self):
        self.objects: dict[str, bytes] = {}
        self.broken = False

    def publish(self, release_dir: Path, version: str, latest=True):
        for path in release_dir.iterdir():
            self.objects[f"{PREFIX}/releases/{version}/{path.name}"] = path.read_bytes()
        if latest:
            self.objects[f"{PREFIX}/latest.txt"] = version.encode()

    def get(self, key, local_path=None):
        if self.broken:
            raise ConnectionError("网络不通")
        data = self.objects[key]
        if local_path:
            Path(local_path).write_bytes(data)
        return data


@pytest.fixture
def puller(tmp_path, monkeypatch):
    module = load("sync_puller", tmp_path, monkeypatch)
    cos = FakeCos()
    alerts: list[str] = []
    data = tmp_path / "w2"
    for name, value in {
        "DATA_DIR": str(data),
        "SECRET_ID": "id",
        "SECRET_KEY": "key",
        "BUCKET": "bucket-for-test",
        "REGION": "ap-test",
        "PREFIX": PREFIX,
        "MIN_DB_BYTES": 0,
        "MIN_CONSENSUS_NAMES": 3,
        "KEEP": 2,
    }.items():
        monkeypatch.setattr(module, name, value)
    monkeypatch.setattr(module, "cos_get", cos.get)
    monkeypatch.setattr(module, "dingtalk_notify", alerts.append)
    module.cos = cos
    module.alerts = alerts
    module.data = data
    module.release = lambda version, rows, **kw: cos.publish(
        build_release(
            tmp_path / "src" / version,
            rows,
            version=version,
            build_ts=kw.pop("build_ts", today_ts()),
            low_confidence=(),
            **kw,
        ),
        version,
    )
    return module


def status(puller):
    return json.loads((puller.data / "status.json").read_text("utf-8"))


def test_switches_after_checks_pass_and_prunes(puller):
    puller.release("v1", names_rows(5))
    assert puller.main() == 0
    assert os.readlink(puller.data / "current") == "releases/v1"
    assert status(puller)["sync"] == "ok" and status(puller)["version"] == "v1"
    puller.release("v2", names_rows(5))
    assert puller.main() == 0
    puller.release("v3", names_rows(5))
    assert puller.main() == 0
    assert sorted(os.listdir(puller.data / "releases")) == ["v2", "v3"]
    assert puller.alerts == []
    # Unchanged latest.txt only refreshes the heartbeat.
    assert puller.main() == 0
    assert status(puller)["note"] == "无变化"


@pytest.mark.parametrize(
    ("kwargs", "reason"),
    [
        ({"manifest_overrides": {"md5": {"program_index.db": "0" * 32}}}, "md5_ok"),
        ({"drop_columns": ("frames",)}, "schema_ok"),
        ({"manifest_overrides": {"hit_rows": 1}}, "manifest_match"),
        ({"build_ts": today_ts(5)}, "fresh"),
    ],
)
def test_bad_release_keeps_previous(puller, kwargs, reason):
    puller.release("v1", names_rows(5))
    assert puller.main() == 0
    puller.release("v2", names_rows(5), **kwargs)
    assert puller.main() == 1
    assert os.readlink(puller.data / "current") == "releases/v1"
    assert reason in status(puller)["reason"]
    assert status(puller)["current"] == "v1"
    assert len(puller.alerts) == 1


def test_drop_over_20_percent_is_rejected(puller):
    puller.release("v1", names_rows(10))
    assert puller.main() == 0
    puller.release("v2", names_rows(7))
    assert puller.main() == 1
    assert "drop_le_20pct" in status(puller)["reason"]


def test_failure_alerts_first_and_third_only(puller):
    puller.release("v1", names_rows(5))
    puller.cos.broken = True
    for _ in range(5):
        puller.main()
    assert status(puller)["fail_count"] == 5
    assert len(puller.alerts) == 2
    assert "首次" in puller.alerts[0] and "连续失败 3 次" in puller.alerts[1]
    puller.cos.broken = False
    assert puller.main() == 0
    assert status(puller)["fail_count"] == 0


def test_no_new_version_alert_is_recorded_and_deduped(puller):
    puller.release("v1", names_rows(5), build_ts=today_ts(1))
    assert puller.main() == 0
    manifest = puller.data / "releases" / "v1" / "manifest.json"
    data = json.loads(manifest.read_text("utf-8"))
    data["build_ts"] = today_ts(3)
    manifest.write_text(json.dumps(data), "utf-8")
    for _ in range(3):
        assert puller.main() == 0
    assert len(puller.alerts) == 1 and "无新版本" in puller.alerts[0]
    assert status(puller)["no_new_alert_ts"]
    # A failure write must not lose the dedupe record either.
    puller.cos.broken = True
    puller.main()
    puller.cos.broken = False
    assert status(puller)["no_new_alert_ts"]
    puller.main()
    assert sum("无新版本" in a for a in puller.alerts) == 1
    # A new version clears it.
    puller.release("v2", names_rows(5))
    assert puller.main() == 0
    assert "no_new_alert_ts" not in status(puller)


def test_missing_config_alerts_once_per_reason(puller, monkeypatch):
    monkeypatch.setattr(puller, "SECRET_KEY", "")
    assert puller.main() == 2
    assert puller.main() == 2
    assert len(puller.alerts) == 1 and "缺少 COS 凭证" in puller.alerts[0]
    monkeypatch.setattr(puller, "SECRET_KEY", "key")
    monkeypatch.setattr(puller, "BUCKET", "")
    assert puller.main() == 2
    assert puller.main() == 2
    assert len(puller.alerts) == 2 and "缺少桶" in puller.alerts[1]
    monkeypatch.setattr(puller, "BUCKET", "bucket-for-test")
    puller.release("v1", names_rows(5))
    assert puller.main() == 0
    assert "config_alert_ts" not in status(puller)
    monkeypatch.setattr(puller, "SECRET_ID", "")
    assert puller.main() == 2
    assert len(puller.alerts) == 3


def test_import_runner_skips_imported_version_and_dedupes(tmp_path, monkeypatch):
    runner = load("w2_import_runner", tmp_path, monkeypatch)
    data = tmp_path / "w2"
    (data / "releases" / "v1").mkdir(parents=True)
    (data / "current").symlink_to("releases/v1")
    deploy = tmp_path / "deploy"
    deploy.mkdir()
    alerts, calls = [], []
    results = iter([(1, "boom"), (1, "boom"), (1, "boom"), (0, "导入完成"), (0, "导入完成")])
    monkeypatch.setattr(runner, "DATA_DIR", str(data))
    monkeypatch.setattr(runner, "STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setattr(runner, "DEPLOY_DIR", str(deploy))
    monkeypatch.setattr(runner, "dingtalk", alerts.append)
    monkeypatch.setattr(runner, "run_import", lambda force: calls.append(force) or next(results))

    assert [runner.main([]) for _ in range(3)] == [1, 1, 1]
    assert len(alerts) == 2  # first failure and third
    assert runner.main([]) == 0
    assert "已上线" in alerts[-1]
    assert runner.main([]) == 0 and len(calls) == 4  # same version: skipped
    assert runner.main(["--force"]) == 0 and calls[-1] is True
    assert len(alerts) == 3  # no second success alert for the same version

    cmd = runner.import_command()
    assert "--no-deps" in cmd and f"{data}:/w2-index:ro" in cmd
    assert cmd[-3:] == ["w2-import", "--source", "/w2-index/current"]


def test_no_secrets_in_ops_files():
    text = "\n".join(p.read_text("utf-8") for p in OPS.iterdir() if p.is_file())
    for needle in ("AKID", "oapi.dingtalk.com/robot/send?access_token=", ".myqcloud.com"):
        assert needle not in text
