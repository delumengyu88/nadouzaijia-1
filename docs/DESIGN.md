# 那豆在架 backend skeleton

This PR is the smallest backend later PRs can build on: import the hourly task export, store the index, and answer a program-name search with server-side entitlement filtering. The mini program and virtual payment stay out of scope. The admin workbench slice is specified at the end of this document.

## Out of scope

- WeChat login, virtual payment, and invite-code redemption. The workbench can read an order and reissue a failed entitlement; it does not take payment.
- Excel hit-list parsing, the hourly COS scan, thumbnail rendering, and the private bucket / CDN.
- **ZIP / bulk download.** Evidence ZIPs are an offline 委托取证 deliverable only. Member batch ZIP (called out in the v1.1 spec as a later idea) is removed. This PR adds no ZIP endpoint, no download-archive table, and no bulk-export model. Per-record download *flags* exist so the permission matrix can say whether a member may download a report or a screenshot later; nothing in this PR returns a file or a ZIP.

## Modules

| Module | Role |
|---|---|
| `nadou.config` | Settings from environment variables |
| `nadou.models` | SQLAlchemy tables |
| `nadou.text` | `TextNormalizer`, `RecordAge` |
| `nadou.filename_parser` | `FilenameParser` |
| `nadou.normalize` | `PlatformNormalizer`, `ProgramNormalizer` |
| `nadou.importer` | `TaskExportImporter` |
| `nadou.records` | `IndexRecordStore` (one row per task + program) |
| `nadou.entitlements` | `EntitlementPolicy` |
| `nadou.signed_url` | `SignedUrlProvider` + `FakeSignedUrlProvider` |
| `nadou.search` | `ProgramSearch` |
| `nadou.main` | FastAPI app |
| `nadou.seed` | Synthetic CSV + index rows through the importer |

Normalization and parsing are classes. `SignedUrlProvider` is the seam the real CDN signer will replace; the API calls only that.

## Data model

Ages use the Beijing calendar date of `tasks.created_at`. If that is null, use `tasks.file_uploaded_at`, then `index_records.uploaded_at`. If all three are null, the row is not treated as younger than 30 days: it stays locked with `检测时间待核实`, is omitted from `platform_count` and from `unverified_source_count`, and does not by itself make the outcome `hit` or `recent_only`. A hit-list row whose `task_id` is absent from the export counts as `done`.

| Table | Why it exists |
|---|---|
| `tasks` | One row per export `task_id`: status, submit time, raw filename, network type, normalized platform. Search ignores `cancelled`, `duplicate`, and `is_test`. |
| `index_records` | One monitored video hitting one program: canonical program name, `consensus` / `dispute`, frame count, timestamps. This is the search result row. |
| `platform_aliases` | Operator map applied to **both** the CSV `平台名` and the filename parser (for example `重庆移动百视通OTT` → `重庆移动`). |
| `program_aliases` | Same fold-and-map rules for indexed names and the search query. |
| `users` | Caller of the search endpoint. `X-User-Id` is a local-only stand-in until WeChat login; it is not production authentication. |
| `entitlements` | Active grants: `regular` / `vip` (expiry), `program_90` / `program_30` (canonical program + expiry), `screenshot` (one record id + expiry). The policy takes the widest live grant. Payment upgrade rules (suspended 普通 days, 7-day renewals) are not implemented; a grant counts when `expires_at` is in the future. |

`tasks.platform_name` is null and `source_unverified` is true when the platform is still unknown. The API shows `来源待核实` only on records the caller is allowed to see. Those rows are not part of the distinct platform count. `is_test` is set when the raw filename, the raw platform, or the canonical platform starts with `test_` (case-insensitive, after full/half-width fold). Test rows are stored so a later export can clear the flag, and they never appear in search.

The search core stops at these tables. The workbench section adds staff, audit, evidence, order, and index-run tables. There is still no ZIP or archive table, and no endpoint returns a file or a download URL.

## Endpoints

1. `GET /health` — process up.
2. `GET /api/v1/programs/search?name=` — canonicalize `name`, return that program's counted records filtered for the caller.

Missing `X-User-Id` is a free caller. An unknown id is 404. **`X-User-Id` is local-only until WeChat login lands.** Do not expose this header as account authentication on a shared or deployed API. There is no import endpoint; import is `TaskExportImporter`, used by the seed script and tests. Workbench upload can call the same class later.

## Import

CSV header, exact names: `task_id,状态,提交时间,filename,公网/专网,平台名`.

- Upsert by `task_id` (re-import updates the row).
- `状态`: `done` / `cancelled` / `duplicate`.
- `提交时间`: `YYYY-MM-DD HH:MM:SS` in Beijing, stored as `created_at`.
- Network and platform columns are used when non-empty. An empty column falls back to `FilenameParser`. Each field falls back on its own.
- The chosen platform string then goes through `PlatformNormalizer` (trim, full/half-width, casefold, alias table). Empty result → `来源待核实`.
- A non-empty network value that is not `公网` or `专网` is not replaced by the parser.

`FilenameParser` reads a leading `公网` / `专网` and the next `-` or whitespace field as the platform. `unknown.mp4`, `1905.mp4`, and `VID_...` have no network prefix and stay unknown.

`ProgramNormalizer` uses the same text fold, then the program alias table, and `dedupe` keeps first-seen canonical names.

## Search filtering

Record age buckets, Beijing calendar days: `<30`, `30–89`, `90–179`, `≥180`. Boundaries are 29/30, 89/90, 179/180.

| Caller | `<30` | `30–89` | `90–179` | `≥180` |
|---|---|---|---|---|
| Free | forensic placeholder | locked | locked | all fields + low-res thumb, no HD, no download |
| 普通 | forensic | locked | full + HD, may download | full + HD, may download |
| VIP | forensic | full + HD, may download | full + HD, may download | full + HD, may download |
| 单节目 90 (this program) | forensic | locked | full + HD, online only | full + HD, online only |
| 单节目 30 (this program) | forensic | full + HD, online only | full + HD, online only | full + HD, online only |
| 单张截图 (this record) | forensic | locked | HD of that shot + download that shot, no other fields | free fields, plus HD and download of that shot |

`<30` is never unlocked. Locked placeholders are only `record_id`, `age_bucket`, `locked`, `lock_reason`. They omit platform, check time, timestamps, and every URL.

`locked_summary` is computed on the server for the caller, so the client does not derive ages. Rows locked only because the check time is missing (`检测时间待核实`) are left out of it.

| Field | Meaning |
|---|---|
| `locked_total` | Locked records with a known age |
| `by_required_tier.member` | Locked records at 90–179 days (普通会员, 单节目 90 天档, or 单张截图) |
| `by_required_tier.vip` | Locked records at 30–89 days (VIP or 单节目 30 天档) |
| `by_required_tier.evidence_only` | Locked records under 30 days (委托取证 only) |
| `unlockable_total` | `member + vip`. `evidence_only` is never included |
| `cheapest_full_unlock` | `single_program_30d` if any vip-bucket record is locked, else `single_program_90d` if any member-bucket record is locked, else `null` |
| `unlocks_by_product` | How many of this program's locked records each product opens. Omitted when the caller already has that access, or when the count is 0 |

Product ids: `screenshot` (one purchase opens one ≥90-day locked record; the count is how many such records are locked), `single_program_90d`, `single_program_30d`, `membership_regular`, `membership_vip`. `single_program_30d` and `membership_vip` open both the vip and member buckets. The other three open only the member bucket. Nothing opens `evidence_only`.

`platform_count` is distinct normalized platforms on counted records with a known age `≥30`. `unverified_source_count` is the number of those records marked `来源待核实`. Rows with no check time are in neither count. Outcome is `hit` when any dated record is `≥30`, `recent_only` when the only dated records are `<30`, otherwise `miss` (an unknown-age placeholder can still be listed). Cancelled, duplicate, and `test_` rows are omitted entirely.

`IndexRecordStore.upsert` stores the program name only after `ProgramNormalizer` (the same fold and alias map search uses), so a later importer can pass a raw or full-width name and still match a search.

Several grants at once: visibility is the widest; report download is only when a membership tier itself covers that age; screenshot download is membership-or-that-screenshot.

## File access

`SignedUrlProvider.sign_derived(variant, record_id, user_id)` signs `lowres`, `card`, or `hd`. Any other variant raises. `FakeSignedUrlProvider` returns `signed://derived/...` with a short expiry and no host. The search handler is the only place that asks for a URL, and only after the policy allows it. Original object URLs are not stored and not returned.

## Local run

Postgres via `docker-compose.yml`, published only on `127.0.0.1:5432`. The API container runs Alembic then uvicorn. `python -m nadou.seed` writes a synthetic export (no real titles, buckets, domains, or hosts), imports it, and inserts matching index rows. It also creates a fixed VIP user id, so it refuses to run unless `NADOU_ALLOW_DEV_SEED=1`. That flag defaults off. Compose sets it on the local API container only.

## Admin workbench

Routes live under `/api/v1/admin`. Hiding a button is not the control: every rule below is enforced on the server. 客服 is the `support` role and 管理员 is the `admin` role. Support receives 403 on admin-only routes.

| Area | Support | Admin |
|---|---|---|
| W02 取证申请, including 沟通备注 | yes | yes |
| W04 用户与会员 | read, phone masked to the last 4 digits | full: staff accounts, ban/unban, manual grant |
| W05 订单 | read, phone masked, no export; may reissue | full, including CSV export and reissue |
| W08 操作日志, index status | no | yes |

W03, W06, W07, and the W09 page are not in this slice. Platform names stay on the lookup side. The hourly index job and `GET /api/v1/admin/index/status` are the backend the W09 page will read.

Search still uses `X-User-Id` as a local stand-in. That header is not workbench authentication.

### Session

Login is `POST /api/v1/admin/auth/login` with username and password. Passwords are stored as Argon2id hashes. The session is an opaque random token stored only as a SHA-256 hash.

The browser transport is the httpOnly cookie `nadou_admin_session` (`SameSite=Lax`, `Path=/api/v1/admin`, `Secure` outside local/test). The login JSON also returns the token so a non-browser client can send `Authorization: Bearer`. If both are present, the bearer token is the one that is used. Logout revokes that session. Sessions last 12 hours.

Five consecutive failures lock that account from that client IP for 30 minutes. Failures from another IP do not lock the account, and failures against one account do not lock a different account on the same IP. The client address is the direct connection address; a forwarded header is not trusted. An admin unlocks with `POST /api/v1/admin/staff/{id}/unlock`, which clears every lock for that account and writes an audit row. The first admin account is inserted out of band through `StaffDirectory.create`. Later accounts are created by an admin.

### Audit log

`audit_logs` is append-only. There is no update or delete route. ORM events reject both, and Postgres triggers (`nadou_reject_mutation`) reject them even for raw SQL. SQLite tests install the same triggers when the tables are created. Each workbench write records actor, time, object type, object id, action, and before/after JSON. Password hashes are not written into the log. Support cannot read the log.

### Evidence requests

`evidence_requests` moves `待受理` → `已受理` → `取证中` → `已交付`, or `已失败` from `已受理` or `取证中`. List filters are status, type, and created time. Overdue requests (still `待受理` after one Beijing working day) sort first, then VIP requesters when `vip_first` is true (the default). A working day is Monday–Friday in Asia/Shanghai; public holidays are not excluded. The deadline keeps the original clock time, so Friday 15:00 is due Monday 15:00.

Marking `取证中` failed requires a reason and a refund method. The refund amount defaults to the confirmed offline payment and may be overridden. Marking `已受理` failed requires a reason and rejects any refund field. Other statuses cannot be failed. Offline delivery methods are `当面`, `快递`, `企业网盘`, and `其他`. `其他` requires a note. Nothing in the response is a URL or a ZIP.

沟通备注 are rows in `evidence_notes`: author, time, and text. Support and admin append them. There is no edit or delete route, and the same append-only guard used by the audit log applies. Detail returns the notes in time order.

`EvidenceLookup.by_application_number` and `GET /api/v1/admin/evidence-requests/by-number/{application_number}` find a request after a user contacts WeChat customer service. There is no in-app chat and no WeChat message push.

`Notifier` is the alert seam. `DingTalkNotifier` reads `NADOU_DINGTALK_WEBHOOK_URL` and `NADOU_DINGTALK_WEBHOOK_SECRET` from the environment and signs the webhook. Neither value is hard-coded. An empty pair disables alerts (`NullNotifier`). A new request notifies immediately. A request notifies again once when it becomes overdue; `overdue_notified_at` stops a second send. Tests use `FakeNotifier`.

### Users, bans, and manual grants

Support lookup returns the phone masked to the last four digits (`90000001234` → `*******1234`). Admin sees the full phone. Ban requires a reason. `temporary` requires an end time; `permanent` rejects one. Unban clears the ban fields. Every change is audited.

Manual grant or extend takes `regular` or `vip`, a day count, and a reason. The resulting row has `entitlements.source = manual`. `EntitlementBook.counts_for_revenue` is false for that source, which is how invite-code conversion and order revenue leave the grant out. An order issuance uses `source = order`.

### Orders and reissue

The order table is a read model until payment exists. `OrderLookup.by_order_number` and `GET /api/v1/admin/orders/by-number/{order_number}` find an order by its public number. Export is `GET /api/v1/admin/orders/export` (CSV, admin only).

`EntitlementIssuer.issue` grants the order's original tier and duration. `order_issuances.order_id` is unique, so a second call returns the existing grant instead of creating another entitlement. The issuer tries three times, then sets the order and the issuance to `发放异常` and notifies once. `POST /api/v1/admin/orders/{id}/reissue` (support or admin) runs that same path only for `发放异常`. The body cannot carry a tier or a duration; the issuance row keeps the snapshot taken on the first attempt. A repeated reissue returns the entitlement already issued.

### Hourly index run

`python -m nadou index-run` pulls the latest task export through `TaskExportSource`. This slice only has `LocalFileTaskExportSource`, which reads `NADOU_TASK_EXPORT_PATH`. The private-storage reader is later and must take its credentials from the environment. The file is passed to `TaskExportImporter`, which upserts by `task_id`, so running the same export again inserts nothing.

Each attempt is an `index_runs` row: start, finish, new rows, `success` or `failed`, and an error summary. Overlap is rejected with a Postgres advisory lock (`pg_try_advisory_lock`). Other databases use the single-row `index_run_locks` table. A lock older than six hours can be taken over so a crashed SQLite process does not block the next hour.

If the newest successful run is more than two days old, the job calls `Notifier` once. The open `index_alert_episodes` row suppresses repeats. A later success closes that episode, and a new gap can alert again. The same job scans overdue evidence requests. `GET /api/v1/admin/index/status` returns `last_successful_index_at` for the future W09 page.
