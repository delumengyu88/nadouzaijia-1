# W2 索引同步 · 那豆侧运维手册

> 大刀侧每天 04:17 重建并校准索引。那豆侧分两步自动接收：
> **拉取器**每小时从 COS 拉最新版本，校验通过后切换 `current`；
> **导入器**每小时把 `current` 导入那豆检索库，小程序马上就能搜到。
> 任何一步失败都自动保留上一版，并在「视频消消乐」钉钉群报警。
> 本手册面向运营/运维同事，日常照着做即可，不需要 AI 参与。

---

## 一、系统是怎么跑的

```
大刀侧（每天 04:17 自动）
  └─ 重建索引 → 校准(剔除停用词/低置信) → manifest + 校验
       → 上传 COS <前缀>/releases/<版本>/ → 更新 <前缀>/latest.txt → 保留3版

那豆侧 ① 拉取器（每小时 :23，systemd：sync-puller.timer，账号 w2sync）
  └─ 读 latest.txt → 与本地 current 比对
       ├─ 相同 → 只刷新心跳，不下载
       └─ 不同 → 下载新版本 → 二次校验 → 原子切换 current 软链 → 保留3版

那豆侧 ② 导入器（每小时 :43，systemd：w2-import.timer，root）
  └─ current 的版本和上次成功导入的一样 → 跳过
     不一样 → 用线上 api 镜像临时起一个容器，只读挂载 /var/lib/w2-index，
              执行 python -m nadou w2-import → 单事务写入检索库
              成功：小程序立即搜到新数据；失败：整笔回滚，继续用上一版
```

| 项目 | 位置 |
|---|---|
| 数据目录 | `/var/lib/w2-index/`（`current` 软链指向 `releases/<版本>/`） |
| 拉取状态 | `/var/lib/w2-index/status.json` |
| 导入状态 | `/var/lib/w2-import/state.json`（只有 root 能读） |
| 脚本和配置 | `/opt/w2-sync-puller/` |
| 拉取日志 | `/var/log/w2-sync-puller/sync_puller.log` |
| 导入日志 | `journalctl -u w2-import.service` |
| 检索库里的版本记录 | 表 `w2_index_imports`；工作台接口 `/api/v1/admin/index/status` 的 `w2_index` 字段 |

**为什么分两个账号**：拉取器只有 COS 只读钥匙，碰不到数据库；数据库密码只在线上 api 容器的环境里，导入器借用 api 镜像执行，宿主机上不另存密码。

---

## 二、装到正式服（第一次，约 20 分钟）

> 这一步会改正式服，必须先得到负责人同意。全程不要把钥匙、webhook 发到聊天里。

1. 把仓库里的 `ops/w2-sync-puller/` 整个目录拷到服务器，例如 `/root/w2-sync-puller/`。
2. 先确认线上 api 已经部署到包含本功能的版本（数据库迁移 `20261123_0030` 已执行）：
   ```bash
   cd <线上部署目录>
   docker compose -p nadou -f compose.prod.yml --env-file .env.prod run --rm --no-deps -T \
     --entrypoint python api -m nadou w2-import --status
   ```
   能打印出 `{"current": null, ...}` 就说明代码和迁移都到位了。
3. 安装：
   ```bash
   cd /root/w2-sync-puller && sudo bash install.sh
   ```
4. **由负责人本人在服务器上填钥匙**（运维把终端交给负责人，或远程共享屏幕）：
   ```bash
   sudo nano /opt/w2-sync-puller/config.env
   ```
   填这几行，保存退出：
   - `W2_COS_SECRET_ID` / `W2_COS_SECRET_KEY`：大刀给的 COS **只读**子账号钥匙
   - `W2_COS_BUCKET`：桶名（大刀提供）
   - `W2_DINGTALK_WEBHOOK`：「视频消消乐」群机器人的 webhook
   - `W2_DINGTALK_SECRET`：只有机器人开了「加签」才填；用「自定义关键词」（关键词填 `W2`）就留空
5. 运维填导入配置：
   ```bash
   sudo nano /opt/w2-sync-puller/import.env
   # NADOU_DEPLOY_DIR=线上部署目录（里面有 compose.prod.yml 和 .env.prod）
   ```
6. 立即跑一次拉取，确认 `sync` 是 `ok`：
   ```bash
   sudo systemctl start sync-puller.service
   cat /var/lib/w2-index/status.json
   ls -l /var/lib/w2-index/current
   ```
7. 立即跑一次导入，确认 `ok` 是 `true`：
   ```bash
   sudo systemctl start w2-import.service
   sudo cat /var/lib/w2-import/state.json
   journalctl -u w2-import.service -n 20 --no-pager
   ```
   日志最后一行应是「导入完成：版本 …，对外共识片名 …」。钉钉群会收到「✅ W2 索引已上线」。
8. 在小程序里搜一个热门片名，确认能出结果。
9. 确认两个定时器都在：`systemctl list-timers sync-puller.timer w2-import.timer`。

---

## 三、日常怎么看状态

```bash
# 定时任务
systemctl list-timers sync-puller.timer w2-import.timer

# 拉取：sync=ok 正常；error 看 reason
cat /var/lib/w2-index/status.json

# 导入：ok=true 正常；version 是最近处理的版本，imported_version 是最近成功导入的版本
sudo cat /var/lib/w2-import/state.json

# 检索库里当前生效的版本（以数据库为准）
cd <线上部署目录>
docker compose -p nadou -f compose.prod.yml --env-file .env.prod run --rm --no-deps -T \
  --entrypoint python api -m nadou w2-import --status
```

`--status` 输出里 `current.version` 就是用户正在搜的版本，`consensus_names` 是对外共识片名数，`last_attempt` 是最近一次尝试（失败时 `error` 有原因）。工作台管理员也可以在 `/api/v1/admin/index/status` 的 `w2_index` 里看到同样内容。

**status.json（拉取）字段**

| 字段 | 含义 |
|---|---|
| `sync` | `ok`=正常 / `error`=失败 |
| `version` | 本次处理的版本（失败时是没切成功的新版本） |
| `current` | 本机当前生效版本 |
| `consensus_names` | 对外共识片名数 |
| `hit_rows` | 命中行总数 |
| `drop_pct` | 相对上一版的跌幅（%） |
| `fail_count` | 连续失败次数 |
| `reason` | 失败原因（仅失败时有） |
| `no_new_alert_ts` | 上次发「36 小时无新版本」报警的时间（用于去重） |
| `config_alert_ts` | 上次发「配置缺失」报警的时间（用于去重） |
| `ts` | 状态时间 |

---

## 四、手动重跑

```bash
sudo systemctl start sync-puller.service   # 立即拉取一次
sudo systemctl start w2-import.service     # 立即导入一次（版本没变会直接跳过）
sudo python3 /opt/w2-sync-puller/w2_import_runner.py --force   # 同一版本也强制重新导入
```

不要用 `sudo -u w2sync python3 ...` 直接跑拉取器：config.env 只有 root 能读，环境变量要由 systemd 注入。

---

## 五、回滚到上一版

回滚分两步：先把 `current` 指回旧版本，再让导入器重新导入。

```bash
# 1. 停掉拉取定时器，免得下一小时又被切回最新版
sudo systemctl stop sync-puller.timer

# 2. 看本地有哪些版本，把 current 指向要回滚的版本
ls -l /var/lib/w2-index/releases/
cd /var/lib/w2-index
sudo -u w2sync ln -sfn releases/20261122_041700 current
ls -l current

# 3. 立即导入（版本变了，导入器会自动导入）
sudo systemctl start w2-import.service
sudo cat /var/lib/w2-import/state.json        # ok=true，version=回滚的版本
```

确认小程序搜索正常。问题解决、想恢复自动更新时：`sudo systemctl start sync-puller.timer`（下一次会拉到最新版并自动导入）。

说明：
- 导入从不删除命中记录。新版本里没有的记录只是不再显示，用户已解锁的权益、查询记录和报告引用都不会断。
- 30 天内的数据回滚时同样锁定，不会因为回滚改变显示状态。

---

## 六、钥匙放在哪、怎么换

- 只在 `/opt/w2-sync-puller/config.env` 一处（root 600）。禁止提交到仓库、禁止发聊天群。
- COS 子账号只能读 `<前缀>/` 下的文件，不能写，也不能列桶根目录。
- 换钥匙：`sudo nano /opt/w2-sync-puller/config.env` 改两行 → `sudo systemctl start sync-puller.service` → `cat /var/lib/w2-index/status.json` 看到 `sync=ok` 即生效。失败时 `reason` 会写「缺少 COS 凭证」或「latest.txt 读取失败」。
- 换钉钉 webhook 也是改这个文件，导入器会自动用新的。

---

## 七、校验规则（为什么有时不切换、不导入）

拉取器切换前检查，任一项不过就保留旧版：

| 校验项 | 规则 |
|---|---|
| 文件大小 | db ≥ 1MB |
| 新鲜度 | build_ts ≤ 2 天 |
| 表结构 | program_index 8 列、program_summary 2 列齐全 |
| 共识片名数 | manifest `consensus_names` ≥ 1500 |
| 与 manifest 一致 | 共识片名数、命中行数对得上 |
| 跌幅 | ≤ 20% |
| MD5 | 与 manifest 一致 |

导入器写库前再核对一次：MD5、表结构、命中行数（`hit_rows`）、共识片名数（`kept_consensus_names`）。对不上就不导入，线上继续用上一版。

---

## 八、导入规则（对外口径）

- 只显示「共识(consensus)」命中；分歧(dispute)/复审(review)只留在后台。同一任务同一片名既有共识又有分歧时，以共识那条为准。
- 停用词（`exclude_list.json` 的 `stopwords`，如「怪」）整片名不导入；低置信词（`low_confidence`，如「家庭/远方/狗」）导入但不显示。`verified_excludes` 只对 30 天前的数据生效。
- 30 天内的数据锁定：之前由 W2 导入并已显示的记录，重建后继续显示、内容不变；之前不显示的继续不显示。全新的记录正常加入。
- 对外片名数 = manifest 里的 `consensus_names`。
- 每条记录按「任务编号 + 片名」唯一，记录编号固定不变，所以重复导入、回滚都不会影响用户的解锁、订单、查询记录和报告。
- 任务的平台、网络类型以每小时任务清单（CSV）为准；W2 只补清单里没有的任务。既没有任务清单、W2 里也没有任务状态的记录暂不导入，等任务清单补上后下次导入自动加入。
- 校验通过的数据直接上线，不需要人工审批。

---

## 九、钉钉报警（自动，发「视频消消乐」群）

| 触发条件 | 谁发 | 去重 |
|---|---|---|
| 拉取/校验失败 | 拉取器 | 首次报一次，连续 3 次再报一次 |
| 超过 36 小时没有新版本 | 拉取器 | 24 小时内只报一次（记录在 status.json） |
| config.env 缺钥匙/桶名/前缀 | 拉取器 | 同一原因 24 小时内只报一次 |
| 导入失败 | 导入器 | 首次报一次，连续 3 次再报一次 |
| 新版本导入成功 | 导入器 | 每个版本报一次 |

---

## 十、常见故障速查

| 现象 | 排查 |
|---|---|
| status.json 长期 error | 看 `reason`，再看 `tail -50 /var/log/w2-sync-puller/sync_puller.log` |
| 「缺少 COS 凭证」「缺少桶/地域/前缀」 | `config.env` 没填全，见第六节 |
| 「latest.txt 读取失败」 | 钥匙失效/无网络/COS 权限被收 |
| 「校验失败: [...]」 | 上游数据异常，保留旧版是正常保护，通知大刀侧查 |
| 导入 state.json 里 ok=false | `journalctl -u w2-import.service -n 50 --no-pager` 看原因；常见是 `NADOU_DEPLOY_DIR` 填错、api 容器起不来、数据库连不上 |
| 「NADOU_DEPLOY_DIR 未配置」 | 编辑 `/opt/w2-sync-puller/import.env` |
| 导入报「MD5 / hit_rows / 缺列」 | 本地文件损坏，删掉该版本目录后 `systemctl start sync-puller.service` 重新拉取 |
| timer 没在跑 | `systemctl status sync-puller.timer w2-import.timer`，必要时 `systemctl restart` |

---

## 十一、故障演练（上线后由运维亲手做一次，约 15 分钟）

目的：确认出问题时能独立回滚。做之前在群里说一声。

1. **记下现状**：
   ```bash
   ls -l /var/lib/w2-index/current           # 记下当前版本 A
   ls /var/lib/w2-index/releases/            # 确认还有一个旧版本 B
   ```
   如果只有一个版本，等第二天有新版本后再做。
2. **回滚到 B**：按第五节第 1–3 步操作。确认 `state.json` 里 `version` 是 B、`ok` 是 true，`--status` 里 `current.version` 是 B，小程序搜索正常。
3. **模拟导入失败**：
   ```bash
   sudo cp /opt/w2-sync-puller/import.env /root/import.env.bak
   sudo sed -i 's|^NADOU_DEPLOY_DIR=.*|NADOU_DEPLOY_DIR="/不存在"|' /opt/w2-sync-puller/import.env
   sudo python3 /opt/w2-sync-puller/w2_import_runner.py --force; echo "退出码 $?"
   ```
   应看到「NADOU_DEPLOY_DIR 未配置或目录不存在」、退出码 2，小程序搜索不受影响。
4. **恢复**：
   ```bash
   sudo cp /root/import.env.bak /opt/w2-sync-puller/import.env
   sudo systemctl start sync-puller.timer
   sudo systemctl start sync-puller.service    # current 切回最新版 A
   sudo systemctl start w2-import.service      # 自动导入 A
   sudo cat /var/lib/w2-import/state.json      # version=A，ok=true
   ```
5. 在群里回报「演练完成」，并写下用时和遇到的问题。
