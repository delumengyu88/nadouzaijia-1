# 那豆在架

Backend skeleton for the 那豆在架 copyright-monitoring lookup service. See [docs/DESIGN.md](docs/DESIGN.md) for the data model, the search permission rules, and what this PR deliberately leaves out (including member ZIP download).

## Local workbench

One command. It builds the API image, starts Postgres, migrates, and loads obvious fake data. The images are the official multi-arch `python` and `postgres` images, so the same command runs on Apple Silicon. Nothing here needs Node.

```bash
docker compose up --build -d
```

Open http://localhost:8000/admin/

| Account | Password | Role |
|---|---|---|
| `演示管理员` | `demo-pass-2026` | 管理员 |
| `演示客服` | `demo-pass-2026` | 客服 |

| Page | Where |
|---|---|
| W01 登录 | `/admin/#/login` |
| W02 取证申请 | `/admin/#/w02` |
| W03 邀请码 | `/admin/#/w03` |
| W04 用户与会员 | `/admin/#/w04` |
| W05 订单 | `/admin/#/w05` |
| W06 内容配置 | `/admin/#/w06` |
| W08 操作日志 | `/admin/#/w08` |
| W09 数据与索引 | `/admin/#/w09` |

侧栏「客服消息 ↗」在第一期打开微信官方客服工具。W07 已取消。W10 客服回复是第二期，不在这个工作台里。

客服只能使用取证申请，以及只读的用户页和订单页；发放异常订单上的补发是订单页的唯一写操作。管理员账号可以看全部页面。

### Fake data and a real task export

`NADOU_DATA_SOURCE=demo` is the default inside Compose. The first start creates the accounts above plus fake users, invite codes, evidence requests, orders, content, audit rows, and a short index. Running the command again does not duplicate them.

To stop loading that fake business data and read a task export instead, set `NADOU_DATA_SOURCE=export` and `NADOU_TASK_EXPORT_PATH` to the CSV mounted into the API container, then start again. In that mode only the demo admin account is created. The hourly job is still `python -m nadou index-run`. Leave the DingTalk variables empty to disable alerts.

Copy `.env.example` only when you run the API on the host instead of in Compose. `NADOU_ALLOW_DEV_SEED` defaults off; Compose turns it on for the local database. `X-User-Id` is a local stand-in until WeChat login.

### Still outside this repo

These are not wired up, and the demo does not pretend they are:

- 官方小程序 AppID
- 小程序 ICP 备案
- 一台服务器和已备案的 https 域名
- 微信支付商户号和虚拟支付
- 大刀的只读索引账号
- 那豆先生书面授权
- 二次 OCR 校准后的首批数据
- CDN 签名（现在是本地假签名）

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
