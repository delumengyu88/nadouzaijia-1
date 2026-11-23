# W2 索引同步 · 那豆侧运维手册

> 本系统每天 04:17 由大刀侧重建并校准索引，那豆侧拉取器每小时自动拉取最新版本，
> 校验通过后原子切换，失败自动保留旧版。本手册面向运营/运维同事，日常按此操作即可，无需 AI 参与。

---

## 一、系统是怎么跑的

```
大刀侧（每天 04:17 自动）
  └─ 重建索引 → 校准(剔除停用词/低置信) → manifest + 4项校验
       → 上传 COS w2-index-export/releases/<版本>/ → 更新 latest.txt → 保留3版

那豆侧（本机，每小时 :23 自动，systemd timer）
  └─ 读 latest.txt → 与本地 current 比对
       ├─ 相同 → 只刷新心跳，不下载
       └─ 不同 → 下载新版本 → 二次校验 → 原子切换 current 软链 → 保留3版
```

- 数据目录：`/var/lib/w2-index/`
- 当前生效版本：`/var/lib/w2-index/current`（软链，指向 `releases/<版本>/`）
- 历史版本：`/var/lib/w2-index/releases/<版本>/`
- 脚本目录：`/opt/w2-sync-puller/`
- 日志：`/var/log/w2-sync-puller/sync_puller.log`（systemd LogsDirectory 自动归 w2sync 所有）
- 状态：`/var/lib/w2-index/status.json`

---

## 二、怎么看状态

```bash
# 1. 看定时任务是否在跑
systemctl status sync-puller.timer
systemctl list-timers sync-puller.timer

# 2. 看最近一次同步结果（json）
cat /var/lib/w2-index/status.json
#    sync=ok 表示正常；sync=error 表示失败，reason 是原因

# 3. 看当前生效版本和本地历史版本
ls -l /var/lib/w2-index/current
ls -l /var/lib/w2-index/releases/

# 4. 看日志（最近 50 行）
tail -50 /var/log/w2-sync-puller/sync_puller.log
```

**status.json 字段含义**

| 字段 | 含义 |
|---|---|
| `sync` | `ok`=正常 / `error`=失败 |
| `version` | 当前生效版本号（形如 `20261123_041700`） |
| `consensus_names` | 对外共识片名数 |
| `hit_rows` | 命中行总数 |
| `drop_pct` | 相对上一版的跌幅（%） |
| `reason` | 失败原因（仅失败时有） |
| `ts` | 状态时间 |

---

## 三、手动重跑（立即同步一次）

```bash
sudo systemctl start sync-puller.service
```

跑完看 `cat /var/lib/w2-index/status.json` 确认结果。退出码：`0`=正常，`1`=校验失败，`2`=内部错误。

> 说明：不要用 `sudo -u w2sync python3 ...` 直接跑——config.env 是 root 600，且环境变量需由 systemd 注入。手动触发一律用 `systemctl start sync-puller.service`。

---

## 四、回滚到上一版

`current` 是一个软链，回滚就是把软链指回上一版本。**先列出历史版本**：

```bash
ls -l /var/lib/w2-index/releases/
```

假设当前 `current` 指向 `20261123_041700`，要回滚到上一版 `20261122_041700`：

```bash
cd /var/lib/w2-index
ln -sfn releases/20261122_041700 current
ls -l current   # 确认已指向目标版本
```

回滚后，下次自动同步若拉取到新版本会再次覆盖。若想「钉死」在某版本不自动更新，需先停掉定时任务：

```bash
sudo systemctl stop sync-puller.timer
```

---

## 五、钥匙放在哪

- 配置文件：`/opt/w2-sync-puller/config.env`（权限 600，仅 root 可读）
- 里面是腾讯云 COS **只读**子账号 `w2-idx-readonly` 的 `W2_COS_SECRET_ID` / `W2_COS_SECRET_KEY`
- 该子账号**只能读** `w2-index-export/` 目录，不能写、不能读其他目录（已隔离验证）
- 服务账号 `w2sync` 通过 systemd 的 `EnvironmentFile` 注入，进程内读取，不落明文日志

> 钥匙明文只在 `config.env` 一处。禁止提交到仓库、禁止发聊天群。

---

## 六、怎么换钥匙

1. 从安全渠道获取新钥匙（新 SecretId / SecretKey）。
2. 编辑配置：

```bash
sudo vi /opt/w2-sync-puller/config.env
# 替换 W2_COS_SECRET_ID / W2_COS_SECRET_KEY 两行
sudo chmod 600 /opt/w2-sync-puller/config.env
```

3. 立即验证新钥匙能否读 COS（用 systemd 触发一次）：

```bash
sudo systemctl start sync-puller.service
cat /var/lib/w2-index/status.json   # sync=ok 即生效
```

4. 若失败，`reason` 会提示「缺少 COS 凭证」或「latest.txt 读取失败」，核对钥匙是否正确、是否已失活。

---

## 七、校验规则（为什么有时不切换）

拉取器每次切换前做 6 项二次校验，**任一项不过就保留旧版、不切换**：

| 校验项 | 规则 | 说明 |
|---|---|---|
| 文件大小 | db ≥ 1MB | 防下载损坏/空文件 |
| 新鲜度 | build_ts ≤ 2 天 | 防拉到过期数据 |
| 表结构 | 两表、8+2 列齐全 | 防半成品 |
| 共识片名数 | ≥ 1500 | 防片名暴跌 |
| 与 manifest 一致 | 数字对得上 | 防文件/清单错配 |
| 跌幅 | ≤ 20% | 相对上一版，防异常缩水 |
| MD5 | 与 manifest 一致 | 防传输损坏 |

大刀侧每天重建时同样有这些校验，不通过就不会更新 `latest.txt`，那豆侧也就拉不到坏数据。**两边双重校验，坏数据到不了线上。**

---

## 八、钉钉报警（自动）

拉取器会向「视频消消乐」群（webhook 在 config.env 的 `W2_DINGTALK_WEBHOOK`）发三种告警：

| 触发条件 | 内容 |
|---|---|
| 校验/拉取失败 | 首次失败报一次（含原因） |
| 连续 3 次失败 | 再报一次升级告警（提示排查上游/凭证） |
| 超过 36 小时无新版本 | 报一次（提示检查大刀侧每日重建），24h 内去重不重复报 |

失败后拉取器会自动保留旧版、不切换，恢复后告警自动停止。

---

## 九、常见故障速查

| 现象 | 排查 |
|---|---|
| status.json 长期 error | 看 `reason`，再看 `/var/log/w2-sync-puller.log` 最后报错 |
| 「缺少 COS 凭证」 | `config.env` 没填钥匙或钥匙被清空 |
| 「latest.txt 读取失败」 | 钥匙失效/无网络/COS 权限被收 |
| 「校验失败: [...]」 | 上游数据异常，保留旧版是正常保护，通知大刀侧查 |
| timer 没在跑 | `systemctl status sync-puller.timer`，必要时 `systemctl restart sync-puller.timer` |

---

## 十、显示规则（对外口径）

- 只显示「共识(consensus)」命中；分歧(dispute)/复审(review)不显示。
- 停用词（如「怪」）整片名剔除；低置信词（「家庭/远方/狗」）不显示。
- 30 天内新数据锁定，不因重建改变显示状态。
- 对外片名数 = manifest 里的 `consensus_names`（当前约 1551）。
