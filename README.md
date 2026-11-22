# 那豆在架

Backend skeleton for the 那豆在架 copyright-monitoring lookup service, plus the phase-1 workbench and the mini program. See [docs/DESIGN.md](docs/DESIGN.md) for the data model and the search permission rules.

## 验收（MacBook Air）

这一节给一台装了 Docker、git 和 python3、没有 Node 的 arm64 MacBook Air。工作台不需要本机安装 Python 或 Node：镜像是官方多架构的 `python:3.12-slim` 和 `postgres:16`。请用下面的 Docker 命令启动。不要在本机用 SQLite 跑 `seed-demo`：迁移里的表结构变更依赖 Postgres，SQLite 会在改约束时失败。

```bash
git clone https://github.com/delumengyu88/nadouzaijia-1.git
cd nadouzaijia-1
git checkout acceptance/2026-11-22
docker compose up --build -d
```

打开 http://localhost:8000/admin/

| 账号 | 密码 | 角色 |
|---|---|---|
| 演示管理员 | demo-pass-2026 | 管理员，可看全部页面 |
| 演示客服 | demo-pass-2026 | 客服：取证申请，以及只读的用户页和订单页。发放异常订单上的补发是订单页唯一的写操作 |

第一次启动会建库、迁移并写入明显的假数据。`NADOU_DATA_SOURCE=demo` 是 Compose 里的默认值。改成 `export` 并设置 `NADOU_TASK_EXPORT_PATH` 后，不再写入假业务数据，只保证演示管理员，索引用那份任务导出表。

### 页面

| 页面 | 地址 |
|---|---|
| W01 登录 | http://localhost:8000/admin/#/login |
| W02 取证申请 | http://localhost:8000/admin/#/w02 |
| W03 邀请码 | http://localhost:8000/admin/#/w03 |
| W04 用户与会员 | http://localhost:8000/admin/#/w04 |
| W05 订单 | http://localhost:8000/admin/#/w05 |
| W06 内容配置 | http://localhost:8000/admin/#/w06 |
| W08 操作日志 | http://localhost:8000/admin/#/w08 |
| W09 数据与索引 | http://localhost:8000/admin/#/w09 |

侧栏「客服消息 ↗」打开微信官方客服工具。W10 客服回复是第二期，不在这个工作台里。

### 小程序

1. 安装微信开发者工具稳定版。
2. 选择「导入项目」，不要选「新建」。
3. 目录选本仓库里的 `miniprogram` 文件夹，不要选仓库根目录。
4. AppID 填 `touristappid`，用测试号或游客模式。不要把正式 AppID 写回仓库。
5. 点「导入」，然后编译。入口是首页。每次冷启动都会看到开屏，右上角「跳过」后进入首页。从后台或分享卡片回到已经在运行的小程序不会再放一次。底部只有三项：首页、会员中心、个人中心。

默认 `miniprogram/config.js` 里 `useMock` 为 `true`，走本地假数据，不访问网络。验收和演示用这个。

要改连本机后端：把 `useMock` 改成 `false`，把 `apiBase` 写成 `http://localhost:8000`。然后在开发者工具的「详情 → 本地设置」里勾选「不校验合法域名」。不勾的话，工具会拦截发到本机的请求。这个地址只留在本机，不要提交。

更细的查询样例和邀请码见 [miniprogram/README.md](miniprogram/README.md)。

### 停止和重置

停止容器，数据还在：

```bash
docker compose down
```

清掉数据库并在下次启动时重新灌入假数据：

```bash
docker compose down -v
docker compose up --build -d
```

### 还缺的外部条件

这些没有接上，演示也不假装已经接上：

- 正式小程序 AppID
- 备案
- 微信支付正式商户联调（服务端 JSAPI 已接，见 [docs/PAYMENTS.md](docs/PAYMENTS.md)；0.01 元测试档默认关闭）
- 大刀的只读索引账号
- CDN 签名（现在是本地假签名）
- 一台服务器和已备案的 https 域名

## Local workbench

The same Compose command is the one-command start. Images are the official multi-arch `python` and `postgres` images. Nothing here needs Node.

```bash
docker compose up --build -d
```

Open http://localhost:8000/admin/ and sign in as `演示管理员` / `demo-pass-2026` or `演示客服` / `demo-pass-2026`.

Copy `.env.example` only when you run the API on the host instead of in Compose. `NADOU_ALLOW_DEV_SEED` defaults off; Compose turns it on for the local database. `X-User-Id` is ignored in every environment. User APIs require an expiring Bearer session from WeChat login; the development seed flag never bypasses authentication.

The workbench API is under `/api/v1/admin`. Staff sign in with a username and password; the session is an httpOnly cookie, and the same token is accepted as a bearer. See [docs/DESIGN.md](docs/DESIGN.md) for roles, lockout, audit, evidence, and orders.

Hourly index import, after the database has been migrated:

```bash
NADOU_TASK_EXPORT_PATH=./export.csv python -m nadou index-run
```

`NADOU_TASK_EXPORT_PATH` is a local CSV. DingTalk alerts use `NADOU_DINGTALK_WEBHOOK_URL` and `NADOU_DINGTALK_WEBHOOK_SECRET` when both are set. Leave them empty to disable alerts. Do not commit real values.

## 创建第一个工作台管理员

生产库的 `staff_accounts` 一开始是空的。数据库迁移完成之后，在服务器上创建管理员。连接用已经配置好的 `DATABASE_URL`。这个命令不读取 `NADOU_ALLOW_DEV_SEED`，也不写入演示账号。

交互式输入密码两次，输入时不会回显：

```bash
python -m nadou create-admin <用户名>
```

脚本从标准输入读一行密码。不要把密码写在命令行参数里：

```bash
python -m nadou create-admin <用户名> < /path/to/password-file
```

生成随机密码，并且只在终端打印一次：

```bash
python -m nadou create-admin <用户名> --generate
```

密码至少 12 个字符，最长 128 个字符。用户名已存在时命令失败退出，不会改掉原来的密码。只有加上 `--reset-password` 才会重置这个管理员的密码。旧密码随即不能登录，这个账号尚未过期的会话一并作废，登录锁定也会清掉。账号还不存在时，`--reset-password` 同样失败，不会顺便新建。同名的客服账号不会被这个命令改掉。审计记录只写下账号名和角色。

不要把真实用户名、密码、地址或数据库连接写进仓库。

## Tests

```bash
pip install -e ".[dev]"
ruff check .
ruff format --check .
pytest
```

The mini program has no build step. CI runs `node scripts/check-miniprogram.js`. That script checks syntax, tags, `wx:for` keys, image paths, and WXSS. `miniprogram-ci` is not in CI: it needs a real AppID and upload key, and `touristappid` cannot stand in for them.


### 短片名待复核

运行 `alembic upgrade head` 将配置表 `review_program_titles` 初始化为家庭、远方、狗，并把这些规范化片名现有的 `consensus` 记录改为 `review`。旧迁移不变。配置表可通过运维数据库操作维护（无工作台入口），按与索引相同的节目名规范化后整词匹配。新的共识 xlsx 导入及通过 `IndexRecordStore` 的增量写入都会读取该表；更改配置后应重新导入受影响记录。任务 CSV 只更新任务元数据，不生成命中记录。

`hit_type=review`（待复核）不会进入用户命中、平台数、锁定统计、解锁价格或用户 xlsx；只有待复核记录的节目显示未命中。`consensus` 为已计入，原有 `dispute` 继续隐藏。本轮不新增审核页面、审核操作接口或 `excluded` 状态。重新导入名单中的共识记录会重新进入待复核。

只读导出待复核 CSV（连接地址来自现有 `DATABASE_URL` 配置）：

```bash
python -m nadou export-review > data/review.csv
```

列为 task_id、片名、平台、命中帧数、时间戳；时间戳列保存 JSON 数组，保留全部命中时间。该文件被 data/ 默认忽略规则覆盖，不提交。命令不改复核状态，不导出图片地址。回滚 0007 会将待复核记录转为原有隐藏的 `dispute`，避免自动变成可计费记录；不能恢复迁移前的可见状态。


## WeChat login

Set `NADOU_WX_APPID` and `NADOU_WX_APPSECRET` in the server environment; `.env.example` intentionally leaves both blank. Run `alembic upgrade head` before starting the API (0008 adds `user_sessions`). Production uses `APP_ENV=production` and `NADOU_ALLOW_DEV_SEED=0`, alongside the existing `DATABASE_URL` configuration.

The mini program sends `POST /api/v1/auth/wechat/login` with `{ "code": "<wx.login code>" }`. The server exchanges it with WeChat and creates or finds the user by openid. The response contains `token`, `token_type: "bearer"`, `expires_at`, and `user` (`id`, `tier`, membership expiry timestamps). `GET /api/v1/auth/me` returns the same user projection for an authenticated session. The server never returns or persists WeChat's session_key and never returns the AppSecret or openid to the client.

Send `Authorization: Bearer <token>` for search/report data, query exports, invite redemption and `/auth/me`. Sessions last seven days; only their SHA-256 digest is stored in the database. Missing, invalid or expired sessions return 401; banned accounts return 403. An invalid/used WeChat code returns 401, malformed input 422, and missing server configuration or upstream failure 503. Staff sessions remain separate and cannot be substituted with a user session.

Keep `touristappid` and an empty `apiBase` in source control. Real WeChat login testing requires the matching mini program identity in local developer settings and a locally configured reachable API; no real credentials or deployment addresses belong in the repository. Python tests mock the WeChat network call, while `node scripts/check-miniprogram.js` includes login state checks for all three adapters. Test identities are minted only by `tests/user_helpers.py`, without a development authentication route.
