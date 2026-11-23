#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
sync_puller.py — 那豆侧 W2 索引同步拉取器（运营长期维护，不依赖 AI）

职责（每小时由 systemd timer 触发）：
  1. 从 COS 读 latest.txt（当前版本指针，几字节）
  2. 与本地 current 版本比对，相同则只刷新心跳状态，跳过下载
  3. 版本变化 → 读 manifest.json → 下载新版本到 staging/
  4. 二次校验：表结构完整 + 共识片名≥1500 + 跌幅≤20% + MD5 + build_ts 新鲜度
  5. 校验通过 → 原子切换 current 软链 → 保留最近 N 版
  6. 校验失败 → 保留旧版，写错误状态

钉钉报警（webhook 从配置读，发「视频消消乐」群）：
  - 校验/拉取失败：首次失败报一次，连续 3 次失败再报一次
  - 超过 36 小时无新版本：报一次（去重）

依赖：pip3 install cos-python-sdk-v5  （仅此一个第三方库）
配置：环境变量（见 config.env.example），由 systemd EnvironmentFile 注入。

退出码：
  0  正常（无变化 或 切换成功）
  1  校验失败（已保留旧版）
  2  内部错误
"""
import os, sys, json, sqlite3, hashlib, shutil, tempfile, datetime, logging, urllib.request

# ---- 配置（环境变量；桶名等一律来自 config.env，不设默认值）----
SECRET_ID = os.environ.get("W2_COS_SECRET_ID", "")
SECRET_KEY = os.environ.get("W2_COS_SECRET_KEY", "")
BUCKET = os.environ.get("W2_COS_BUCKET", "")
REGION = os.environ.get("W2_COS_REGION", "")
PREFIX = os.environ.get("W2_COS_PREFIX", "")
DATA_DIR = os.environ.get("W2_DATA_DIR", "/var/lib/w2-index")
KEEP = int(os.environ.get("W2_KEEP", "3"))
LOG_FILE = os.environ.get("W2_LOG_FILE", "/var/log/w2-sync-puller/sync_puller.log")
WEBHOOK = os.environ.get("W2_DINGTALK_WEBHOOK", "")

# 校验阈值
MIN_CONSENSUS_NAMES = 1500
MAX_DROP_PCT = 20.0
MAX_BUILD_AGE_DAYS = 2            # build_ts 超过 2 天视为过期
MIN_DB_BYTES = 1_000_000          # db 至少 1MB，防下载损坏/空文件
NO_NEW_VERSION_HOURS = 36         # 超过该小时数无新版本则报警
NO_NEW_ALERT_COOLDOWN_HOURS = 24  # 36h 报警冷却（去重）
FAIL_ALERT_COUNT = 3              # 连续失败达到该次数再报升级告警

REQUIRED_SCHEMA = {
    "program_index": ["program_name", "task_id", "hit_type", "frames",
                      "timestamps", "created_at", "filename", "status"],
    "program_summary": ["program_name", "hit_count"],
}
FILES = ["program_index.db", "program_index.json", "consensus_names.csv",
         "consensus_names_with_count.csv", "manifest.json", "exclude_list.json"]


def _ensure_log():
    d = os.path.dirname(LOG_FILE)
    try:
        os.makedirs(d, exist_ok=True)
    except Exception:
        pass


_ensure_log()
logging.basicConfig(filename=LOG_FILE, level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("sync_puller")


def dingtalk_notify(text):
    if not WEBHOOK:
        return
    try:
        payload = json.dumps({"msgtype": "markdown",
                              "markdown": {"title": "W2同步告警", "text": text}},
                             ensure_ascii=False)
        req = urllib.request.Request(WEBHOOK, data=payload.encode("utf-8"),
                                     headers={"Content-Type": "application/json"})
        urllib.request.urlopen(req, timeout=10)
    except Exception as e:
        log.error("钉钉通知失败: %s", e)


def cos():
    from qcloud_cos import CosConfig, CosS3Client
    cfg = CosConfig(Region=REGION, SecretId=SECRET_ID, SecretKey=SECRET_KEY)
    return CosS3Client(cfg)


def cos_get(key, local_path=None):
    c = cos()
    resp = c.get_object(Bucket=BUCKET, Key=key)
    data = resp["Body"].get_raw_stream().read()
    if local_path:
        with open(local_path, "wb") as f:
            f.write(data)
    return data


def cos_get_text(key):
    return cos_get(key).decode("utf-8").strip()


def md5_file(path):
    h = hashlib.md5()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def verify_schema(db_path):
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    cur = conn.cursor()
    cols = {}
    try:
        for tbl in REQUIRED_SCHEMA:
            cols[tbl] = [r[1] for r in cur.execute(f"PRAGMA table_info({tbl})")]
    except Exception:
        conn.close()
        return False, "db 打开失败"
    conn.close()
    for tbl, required in REQUIRED_SCHEMA.items():
        if tbl not in cols:
            return False, f"缺表 {tbl}"
        missing = [c for c in required if c not in cols[tbl]]
        if missing:
            return False, f"表 {tbl} 缺列 {missing}"
    return True, "ok"


def db_counts(db_path):
    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        cur = conn.cursor()
        cons = cur.execute("SELECT COUNT(DISTINCT program_name) FROM program_index "
                           "WHERE hit_type='consensus'").fetchone()[0]
        rows = cur.execute("SELECT COUNT(*) FROM program_index").fetchone()[0]
        conn.close()
        return cons, rows
    except Exception:
        return None, None


def current_version():
    link = os.path.join(DATA_DIR, "current")
    if os.path.islink(link):
        return os.path.basename(os.readlink(link))
    return ""


def read_local_manifest(version):
    p = os.path.join(DATA_DIR, "releases", version, "manifest.json")
    if os.path.exists(p):
        with open(p, encoding="utf-8") as f:
            return json.load(f)
    return None


def read_status():
    p = os.path.join(DATA_DIR, "status.json")
    try:
        with open(p, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def write_status(obj):
    path = os.path.join(DATA_DIR, "status.json")
    os.makedirs(DATA_DIR, exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def atomic_switch(version):
    link = os.path.join(DATA_DIR, "current")
    tmp_link = os.path.join(DATA_DIR, "current.tmp")
    target = os.path.join("releases", version)
    if os.path.islink(tmp_link) or os.path.exists(tmp_link):
        os.remove(tmp_link)
    os.symlink(target, tmp_link)
    os.replace(tmp_link, link)
    return os.readlink(link) == target


def prune_old():
    rel = os.path.join(DATA_DIR, "releases")
    if not os.path.isdir(rel):
        return
    vers = sorted([d for d in os.listdir(rel) if os.path.isdir(os.path.join(rel, d))])
    while len(vers) > KEEP:
        old = vers.pop(0)
        if old != current_version():
            shutil.rmtree(os.path.join(rel, old), ignore_errors=True)
            log.info("删除旧版本: %s", old)


def alert_on_fail(fail_count, reason):
    """失败报警：首次 + 连续 N 次各报一次，避免每小时刷屏。"""
    if fail_count == 1:
        dingtalk_notify(f"## ⚠️ W2 索引同步失败（首次）\n\n- 原因: {reason}\n- 处理: 已保留旧版，未切换")
    elif fail_count == FAIL_ALERT_COUNT:
        dingtalk_notify(f"## 🚨 W2 索引同步连续失败 {fail_count} 次\n\n- 最近原因: {reason}\n- 请排查上游重建或 COS 凭证")


def check_no_new_version(cur, prev_status):
    """超过 NO_NEW_VERSION_HOURS 小时无新版本则报警（冷却去重）。"""
    pm = read_local_manifest(cur)
    if not pm:
        return
    build_ts = pm.get("build_ts", "")
    try:
        build_dt = datetime.datetime.strptime(build_ts[:10], "%Y-%m-%d")
    except Exception:
        return
    age_hours = (datetime.datetime.now() - build_dt).total_seconds() / 3600
    if age_hours <= NO_NEW_VERSION_HOURS:
        return
    last_alert = prev_status.get("no_new_alert_ts", "")
    if last_alert:
        try:
            last_dt = datetime.datetime.strptime(last_alert[:19], "%Y-%m-%d %H:%M:%S")
            if (datetime.datetime.now() - last_dt).total_seconds() / 3600 < NO_NEW_ALERT_COOLDOWN_HOURS:
                return
        except Exception:
            pass
    dingtalk_notify(f"## ⏰ W2 索引超过 {NO_NEW_VERSION_HOURS} 小时无新版本\n\n- 当前版本: {cur}\n- 版本时间: {build_ts}\n- 请检查大刀侧每日重建是否正常")


def main():
    now = lambda: datetime.datetime.now().strftime("%F %T")
    os.makedirs(os.path.join(DATA_DIR, "releases"), exist_ok=True)

    if not SECRET_ID or not SECRET_KEY:
        log.error("缺少 COS 凭证（W2_COS_SECRET_ID/W2_COS_SECRET_KEY）")
        write_status({"sync": "error", "ts": now(), "reason": "缺少 COS 凭证"})
        return 2
    if not BUCKET or not REGION or not PREFIX:
        log.error("缺少桶/地域/前缀配置（W2_COS_BUCKET/W2_COS_REGION/W2_COS_PREFIX）")
        write_status({"sync": "error", "ts": now(), "reason": "缺少桶名配置"})
        return 2

    prev_status = read_status()
    prev_fail_count = prev_status.get("fail_count", 0)

    # 1. 读 latest.txt
    try:
        latest = cos_get_text(f"{PREFIX}/latest.txt")
    except Exception as e:
        fail_count = prev_fail_count + 1
        log.error("读取 latest.txt 失败: %s", e)
        write_status({"sync": "error", "ts": now(), "fail_count": fail_count,
                      "reason": f"latest.txt 读取失败: {e}"})
        alert_on_fail(fail_count, f"读取 latest.txt 失败: {e}")
        return 2

    cur = current_version()
    log.info("latest=%s current=%s fail_count=%s", latest, cur, prev_fail_count)

    if latest == cur:
        # 无变化：刷新心跳 + 36h 无新版本检测
        write_status({"sync": "ok", "version": cur, "ts": now(),
                      "fail_count": 0, "note": "无变化"})
        check_no_new_version(cur, prev_status)
        return 0

    # 2. 读 manifest
    try:
        manifest = json.loads(cos_get_text(f"{PREFIX}/releases/{latest}/manifest.json"))
    except Exception as e:
        fail_count = prev_fail_count + 1
        log.error("读取 manifest 失败: %s", e)
        write_status({"sync": "error", "version": latest, "ts": now(),
                      "fail_count": fail_count, "reason": f"manifest 读取失败: {e}"})
        alert_on_fail(fail_count, f"manifest 读取失败: {e}")
        return 2

    # 3. 下载到 staging + 校验 + 切换
    staging = tempfile.mkdtemp(prefix="w2sync_", dir=DATA_DIR)
    try:
        for fn in FILES:
            cos_get(f"{PREFIX}/releases/{latest}/{fn}",
                    os.path.join(staging, fn))
        db_path = os.path.join(staging, "program_index.db")

        # 4. 二次校验
        checks = {}
        db_size = os.path.getsize(db_path)
        checks["size_ok"] = db_size >= MIN_DB_BYTES
        build_ts = manifest.get("build_ts", "")
        try:
            build_date = datetime.datetime.strptime(build_ts[:10], "%Y-%m-%d").date()
            checks["fresh"] = (datetime.date.today() - build_date).days <= MAX_BUILD_AGE_DAYS
        except Exception:
            checks["fresh"] = False
        schema_ok, schema_msg = verify_schema(db_path)
        checks["schema_ok"] = schema_ok
        cons_cnt, rows_cnt = db_counts(db_path)
        checks["db_readable"] = (cons_cnt is not None and rows_cnt is not None)
        checks["names_ge_1500"] = manifest.get("consensus_names", 0) >= MIN_CONSENSUS_NAMES
        checks["manifest_match"] = (checks["db_readable"] and
                                    cons_cnt == manifest.get("kept_consensus_names") and
                                    rows_cnt == manifest.get("hit_rows"))
        drop_pct = 0.0
        prev_cons = None
        if cur:
            pm = read_local_manifest(cur)
            if pm:
                prev_cons = pm.get("consensus_names")
                if prev_cons:
                    drop_pct = round((prev_cons - manifest.get("consensus_names", 0)) / prev_cons * 100, 2)
        checks["drop_le_20pct"] = (drop_pct <= MAX_DROP_PCT)
        want_md5 = manifest.get("md5", {}).get("program_index.db", "")
        checks["md5_ok"] = (want_md5 and md5_file(db_path) == want_md5)

        log.info("校验: %s", json.dumps(checks, ensure_ascii=False))

        if not all(checks.values()):
            reason = [k for k, v in checks.items() if not v]
            fail_count = prev_fail_count + 1
            log.error("校验失败: %s（保留旧版 %s）", reason, cur)
            write_status({"sync": "error", "version": latest, "ts": now(),
                          "fail_count": fail_count,
                          "reason": f"校验失败: {reason}，保留旧版 {cur}"})
            alert_on_fail(fail_count, f"校验失败: {reason}")
            shutil.rmtree(staging, ignore_errors=True)
            return 1

        # 5. 原子切换
        final_dir = os.path.join(DATA_DIR, "releases", latest)
        if os.path.exists(final_dir):
            shutil.rmtree(final_dir, ignore_errors=True)
        os.replace(staging, final_dir)
        if not atomic_switch(latest):
            fail_count = prev_fail_count + 1
            log.error("原子切换失败")
            write_status({"sync": "error", "ts": now(), "fail_count": fail_count,
                          "reason": "原子切换失败"})
            alert_on_fail(fail_count, "原子切换失败")
            return 1

        prune_old()
        log.info("✅ 切换成功 %s（对外共识片名 %s，命中行 %s）",
                 latest, manifest.get("consensus_names"), rows_cnt)
        write_status({"sync": "ok", "version": latest, "ts": now(),
                      "consensus_names": manifest.get("consensus_names"),
                      "hit_rows": rows_cnt, "drop_pct": drop_pct,
                      "fail_count": 0, "note": "切换成功"})
        return 0
    except Exception as e:
        fail_count = prev_fail_count + 1
        log.error("拉取/校验异常: %s（保留旧版 %s）", e, cur)
        write_status({"sync": "error", "version": latest, "ts": now(),
                      "fail_count": fail_count,
                      "reason": f"拉取/校验异常: {e}，保留旧版 {cur}"})
        alert_on_fail(fail_count, f"拉取/校验异常: {e}")
        return 1
    finally:
        if os.path.exists(staging):
            shutil.rmtree(staging, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
