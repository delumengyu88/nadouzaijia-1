#!/usr/bin/env python3
"""
w2_import_runner.py — 把拉取器切换好的 W2 版本导入那豆检索库（宿主机 root 运行）

每小时由 w2-import.timer 触发（:43，比拉取器 :23 晚 20 分钟）：
  1. 读 /var/lib/w2-index/current 指向的版本
  2. 和上次成功导入的版本一样 → 什么都不做
  3. 不一样（新版本，或运维刚回滚了 current）→ 在 api 镜像里跑一次
     python -m nadou w2-import（只读挂载 /var/lib/w2-index）
  4. 结果写 /var/lib/w2-import/state.json；失败发钉钉（首次 + 连续 3 次），成功每个版本报一次

数据库密码只在 api 容器的环境里，拉取器的 w2sync 账号碰不到。
钉钉 webhook 复用 /opt/w2-sync-puller/config.env 里的 W2_DINGTALK_WEBHOOK，不另存一份。

用法：
  python3 w2_import_runner.py           # 正常运行（systemd 调用）
  python3 w2_import_runner.py --force   # 同一版本也重新导入（回滚演练用）
退出码：0 正常/无需导入；1 导入失败（线上继续用上一版数据）；2 配置问题
"""

import base64
import datetime
import hashlib
import hmac
import json
import os
import subprocess
import sys
import time
import urllib.parse
import urllib.request

IMPORT_CONFIG = os.environ.get("W2_IMPORT_CONFIG", "/opt/w2-sync-puller/import.env")


def _load_import_config():
    """手动运行（不经 systemd）时也读 import.env；已有的环境变量优先。"""
    try:
        with open(IMPORT_CONFIG, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    key, value = line.split("=", 1)
                    os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))
    except OSError:
        pass


_load_import_config()
DATA_DIR = os.environ.get("W2_DATA_DIR", "/var/lib/w2-index")
STATE_DIR = os.environ.get("W2_IMPORT_STATE_DIR", "/var/lib/w2-import")
PULLER_CONFIG = os.environ.get("W2_PULLER_CONFIG", "/opt/w2-sync-puller/config.env")
DEPLOY_DIR = os.environ.get("NADOU_DEPLOY_DIR", "")
COMPOSE_FILE = os.environ.get("NADOU_COMPOSE_FILE", "compose.prod.yml")
ENV_FILE = os.environ.get("NADOU_ENV_FILE", ".env.prod")
PROJECT = os.environ.get("NADOU_COMPOSE_PROJECT", "nadou")
SERVICE = os.environ.get("NADOU_API_SERVICE", "api")
TIMEOUT_SECONDS = int(os.environ.get("W2_IMPORT_TIMEOUT", "1800"))
FAIL_ALERT_COUNT = 3
TS_FORMAT = "%Y-%m-%d %H:%M:%S"


def now():
    return datetime.datetime.now().strftime(TS_FORMAT)


def read_env_file(path):
    """读 KEY=value 格式文件（systemd EnvironmentFile 同款），只取需要的键。"""
    values = {}
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, value = line.split("=", 1)
                values[key.strip()] = value.strip().strip('"').strip("'")
    except OSError:
        pass
    return values


def dingtalk(text):
    cfg = read_env_file(PULLER_CONFIG)
    url = cfg.get("W2_DINGTALK_WEBHOOK", "")
    secret = cfg.get("W2_DINGTALK_SECRET", "")
    if not url:
        return
    if secret:
        ts = str(int(time.time() * 1000))
        digest = hmac.new(secret.encode(), f"{ts}\n{secret}".encode(), hashlib.sha256).digest()
        sign = urllib.parse.quote_plus(base64.b64encode(digest))
        url = f"{url}{'&' if '?' in url else '?'}timestamp={ts}&sign={sign}"
    payload = json.dumps(
        {"msgtype": "markdown", "markdown": {"title": "W2导入", "text": text}},
        ensure_ascii=False,
    ).encode("utf-8")
    try:
        req = urllib.request.Request(
            url, data=payload, headers={"Content-Type": "application/json"}
        )
        urllib.request.urlopen(req, timeout=10).read()
    except Exception as exc:  # noqa: BLE001 - 报警失败不影响导入结果
        print(f"钉钉通知失败: {exc}", file=sys.stderr)


def current_version():
    link = os.path.join(DATA_DIR, "current")
    if not os.path.islink(link):
        return ""
    return os.path.basename(os.path.realpath(link))


def read_state():
    try:
        with open(os.path.join(STATE_DIR, "state.json"), encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def write_state(state):
    os.makedirs(STATE_DIR, exist_ok=True)
    path = os.path.join(STATE_DIR, "state.json")
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def import_command():
    return [
        "docker",
        "compose",
        "-p",
        PROJECT,
        "-f",
        os.path.join(DEPLOY_DIR, COMPOSE_FILE),
        "--env-file",
        os.path.join(DEPLOY_DIR, ENV_FILE),
        "run",
        "--rm",
        "--no-deps",
        "-T",
        "-v",
        f"{DATA_DIR}:/w2-index:ro",
        "--entrypoint",
        "python",
        SERVICE,
        "-m",
        "nadou",
        "w2-import",
        "--source",
        "/w2-index/current",
    ]


def run_import(force):
    cmd = import_command() + (["--force"] if force else [])
    proc = subprocess.run(
        cmd, cwd=DEPLOY_DIR, capture_output=True, text=True, timeout=TIMEOUT_SECONDS
    )
    output = (proc.stdout + proc.stderr).strip()
    return proc.returncode, output


def main(argv=None):
    force = "--force" in (sys.argv[1:] if argv is None else argv)
    if not DEPLOY_DIR or not os.path.isdir(DEPLOY_DIR):
        print("NADOU_DEPLOY_DIR 未配置或目录不存在（见 /opt/w2-sync-puller/import.env）")
        return 2
    version = current_version()
    if not version:
        print("还没有生效版本（current 不存在），等拉取器第一次切换成功")
        return 0
    state = read_state()
    if not force and state.get("version") == version and state.get("ok"):
        print(f"版本 {version} 已导入，跳过")
        return 0

    try:
        code, output = run_import(force)
    except subprocess.TimeoutExpired:
        code, output = 1, f"导入超过 {TIMEOUT_SECONDS} 秒未结束"
    except OSError as exc:
        code, output = 1, f"无法执行 docker compose：{exc}"
    print(output)
    tail = output[-400:]

    if code == 0:
        if state.get("imported_version") != version:
            dingtalk(f"## ✅ W2 索引已上线\n\n- 版本: {version}\n- {tail[-200:]}")
        write_state(
            {
                "version": version,
                "imported_version": version,
                "ok": True,
                "ts": now(),
                "fail_count": 0,
                "message": tail,
            }
        )
        return 0

    fail_count = int(state.get("fail_count") or 0) + 1
    if fail_count in (1, FAIL_ALERT_COUNT):
        dingtalk(
            f"## ⚠️ W2 索引导入失败（第 {fail_count} 次）\n\n- 版本: {version}\n"
            f"- 原因: {tail[-300:]}\n- 线上继续使用上一版数据，运维请按手册「导入失败」处理"
        )
    write_state(
        {
            "version": version,
            "imported_version": state.get("imported_version"),
            "ok": False,
            "ts": now(),
            "fail_count": fail_count,
            "message": tail,
        }
    )
    return 1


if __name__ == "__main__":
    sys.exit(main())
