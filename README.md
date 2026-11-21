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
| W07 | 已取消，没有这一页 |
| W08 操作日志 | http://localhost:8000/admin/#/w08 |
| W09 数据与索引 | http://localhost:8000/admin/#/w09 |

侧栏「客服消息 ↗」打开微信官方客服工具。W10 客服回复是第二期，不在这个工作台里。

### 小程序

1. 安装微信开发者工具稳定版。
2. 选择「导入项目」，不要选「新建」。
3. 目录选本仓库里的 `miniprogram` 文件夹，不要选仓库根目录。
4. AppID 填 `touristappid`，用测试号或游客模式。不要把正式 AppID 写回仓库。
5. 点「导入」，然后编译。入口是首页。第一次打开会看到开屏，右下角「跳过」后进入首页。底部只有三项：首页、会员中心、个人中心。

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
- 微信支付商户号
- 大刀的只读索引账号
- CDN 签名（现在是本地假签名）
- 一台服务器和已备案的 https 域名

## Local workbench

The same Compose command is the one-command start. Images are the official multi-arch `python` and `postgres` images. Nothing here needs Node.

```bash
docker compose up --build -d
```

Open http://localhost:8000/admin/ and sign in as `演示管理员` / `demo-pass-2026` or `演示客服` / `demo-pass-2026`.

Copy `.env.example` only when you run the API on the host instead of in Compose. `NADOU_ALLOW_DEV_SEED` defaults off; Compose turns it on for the local database. `X-User-Id` is a local stand-in until WeChat login.

The workbench API is under `/api/v1/admin`. Staff sign in with a username and password; the session is an httpOnly cookie, and the same token is accepted as a bearer. See [docs/DESIGN.md](docs/DESIGN.md) for roles, lockout, audit, evidence, and orders.

Hourly index import, after the database has been migrated:

```bash
NADOU_TASK_EXPORT_PATH=./export.csv python -m nadou index-run
```

`NADOU_TASK_EXPORT_PATH` is a local CSV. DingTalk alerts use `NADOU_DINGTALK_WEBHOOK_URL` and `NADOU_DINGTALK_WEBHOOK_SECRET` when both are set. Leave them empty to disable alerts. Do not commit real values.

## Tests

```bash
pip install -e ".[dev]"
ruff check .
ruff format --check .
pytest
```

The mini program has no build step. CI runs `node scripts/check-miniprogram.js`. That script checks syntax, tags, `wx:for` keys, image paths, and WXSS. `miniprogram-ci` is not in CI: it needs a real AppID and upload key, and `touristappid` cannot stand in for them.
