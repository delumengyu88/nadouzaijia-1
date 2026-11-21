# 那豆在架 backend skeleton

This PR is the smallest backend later PRs can build on: import the hourly task export, store the index, and answer a program-name search with server-side entitlement filtering. The mini program and virtual payment stay out of scope. The admin workbench slice is specified at the end of this document.

## Out of scope

- WeChat login, virtual payment, and invite-code redemption. The workbench can read an order and reissue a failed entitlement; it does not take payment.
- Excel hit-list parsing, the hourly COS scan, thumbnail rendering, and the private bucket / CDN.
- **ZIP / bulk download.** Evidence ZIPs stay an offline 委托取证 deliverable. Member batch screenshot ZIP stays out: no ZIP route, no download-archive table, and no bulk-export model. Search still returns no file. The query export below can return one `.xlsx`, or a zip of spreadsheet parts when that workbook would exceed the size limit. That zip is only the split workbook.

## Modules

| Module | Role |
|---|---|
| `nadou.config` | Settings from environment variables |
| `nadou.models` | SQLAlchemy tables |
| `nadou.text` | `TextNormalizer`, `RecordAge` |
| `nadou.filename_parser` | `FilenameParser` |
| `nadou.platform_names` | `PlatformNames`, the fixed platform map |
| `nadou.normalize` | `ProgramNormalizer` |
| `nadou.importer` | `TaskExportImporter` |
| `nadou.records` | `IndexRecordStore` (one row per task + program) |
| `nadou.entitlements` | `EntitlementPolicy` |
| `nadou.signed_url` | `SignedUrlProvider` + `FakeSignedUrlProvider` |
| `nadou.frames` | Derived-frame bytes, watermark, JPEG |
| `nadou.search` | `ProgramSearch` |
| `nadou.query_export` | `QueryExport` workbook |
| `nadou.main` | FastAPI app |
| `nadou.seed` | Synthetic CSV + index rows through the importer |

Normalization and parsing are classes. `SignedUrlProvider` is the seam the real CDN signer will replace; the API calls only that.

## Data model

Ages use the Beijing calendar date of `tasks.created_at`. If that is null, use `tasks.file_uploaded_at`, then `index_records.uploaded_at`. If all three are null, the row is not treated as younger than 30 days: it stays locked with `检测时间待核实`, is omitted from `platform_count` and from `unverified_source_count`, and does not by itself make the outcome `hit` or `recent_only`. A hit-list row whose `task_id` is absent from the export counts as `done`.

| Table | Why it exists |
|---|---|
| `tasks` | One row per export `task_id`: status, submit time, raw filename, network type, normalized platform. Search ignores `cancelled`, `duplicate`, and `is_test`. |
| `index_records` | One monitored video hitting one program: canonical program name, `consensus` / `dispute`, frame count, timestamps. This is the search result row. |
| `platform_aliases` | Unused by search. Platform names come from `PlatformNames` in code, not from this table and not from an admin screen. |
| `program_aliases` | Same fold-and-map rules for indexed names and the search query. |
| `users` | Caller of the search endpoint. `X-User-Id` is a local-only stand-in until WeChat login; it is not production authentication. |
| `entitlements` | Active grants: `regular` / `vip` (expiry), `program_90` / `program_30` (canonical program + expiry), `screenshot` (one record id + expiry). The policy takes the widest live grant. Payment upgrade rules (suspended 普通 days, 7-day renewals) are not implemented; a grant counts when `expires_at` is in the future. |
| `member_queries` | One saved lookup. `id` is the workbook 查询编号. |
| `query_programs` | Uploaded names in that lookup: optional 节目类型, matched canonical name, resolution (`exact` / `alias` / `manual` / `miss` / `pending` / `failed`), and the 说明 text. |

`PlatformNames` maps a variant such as `GD移动` to operator `移动` and province `广东`. OTT and 百视通 are stored in `platform_subtype` and are not part of the count. The same class is used for the CSV `平台名` and for the filename parser. A name that cannot be parsed leaves the three fields null and `source_unverified` true. `is_test` is set when the raw filename, the raw platform, or the canonical platform starts with `test_` (case-insensitive, after full/half-width fold). Test rows are stored so a later export can clear the flag, and they never appear in search or in the workbook export.

The API shows `平台未识别` only on records the caller is allowed to see. Those rows still count as hits, and they are not part of the distinct platform count. The workbench section adds staff, audit, evidence, order, and index-run tables. There is still no evidence ZIP or download-archive table.

## Endpoints

1. `GET /health` — process up.
2. `GET /api/v1/programs/search?name=` — canonicalize `name`, return that program's counted records filtered for the caller.
3. `GET /api/v1/queries/{query_id}/export` — workbook for one saved query. See below. Same `X-User-Id` stand-in.

Missing `X-User-Id` is a free caller. An unknown id is 404. **`X-User-Id` is local-only until WeChat login lands.** Do not expose this header as account authentication on a shared or deployed API. There is no import endpoint; import is `TaskExportImporter`, used by the seed script and tests. Workbench upload can call the same class later.

## Import

CSV header, exact names: `task_id,状态,提交时间,filename,公网/专网,平台名`.

- Upsert by `task_id` (re-import updates the row).
- `状态`: `done` / `cancelled` / `duplicate`.
- `提交时间`: `YYYY-MM-DD HH:MM:SS` in Beijing, stored as `created_at`.
- Network and platform columns are used when non-empty. An empty column falls back to `FilenameParser`. Each field falls back on its own.
- The chosen platform string then goes through `PlatformNames` (trim, full/half-width, casefold, alias such as `GD移动`, then province/operator/subtype). Unparsed names → `平台未识别`.
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

The index stores titles only. One normalized title is one program, so search returns all of that title's records together. There is no `ambiguous` outcome.

`platform_count` is distinct province+operator pairs on records with a known age `≥30`. 广东移动 and 浙江移动 are two. OTT, 百视通, 公网, and 专网 are display tags on the same pair and do not add to the count. A name that cannot be parsed is still a hit, shown as `平台未识别`, and is left out of `platform_count`. `unverified_source_count` is how many of those aged rows are unrecognized. Rows with no check time are in neither count.

`ProgramTitleGuard` is a configurable stop-word hook. The built-in list is empty. A blocked title is `miss` with an empty `locked_summary` and is not priced. There is no character-length cutoff.

Search returns one program title, then `platform_groups` keyed by province+operator. Each open record carries `platform_subtype` and, when the old consensus sheet had it, `category`. `AI分歧` and `非共识节目` are not indexed. Locked rows stay in `locked_records` with no platform, subtype, or category.

Outcome is `hit` when any dated record is `≥30`, `recent_only` when the only dated records are `<30`, otherwise `miss` (an unknown-age placeholder can still be listed). Cancelled, duplicate, and `test_` rows are omitted entirely.

`IndexRecordStore.upsert` stores the program name only after `ProgramNormalizer` (the same fold and alias map search uses), so a later importer can pass a raw or full-width name and still match a search. That match is a normal confirmed hit. The search response has no `match_type`, alias, or pending field, and it does not say the hit came from an alias.

Only `hit_type=consensus` is returned. A `dispute` row (疑似 / not yet confirmed) is not a user hit and is left out of counts. The words 待确认 and 疑似 are not sent to the client.

A locked row is an opaque placeholder: `record_id`, `age_bucket`, `locked`, `lock_reason`. It has no program name, platform, screenshot, or URL. The counts live only in `locked_summary`. The frontend draws the blurred card from that placeholder.

Several grants at once: visibility is the widest; report download is only when a membership tier itself covers that age; screenshot download is membership-or-that-screenshot. Export is stricter than view: only a live `regular` or `vip` grant may download the workbook, and a program grant or a screenshot grant alone does not.

## File access

`SignedUrlProvider.sign_derived(variant, record_id, user_id)` signs `lowres`, `card`, or `hd`. Any other variant raises. `FakeSignedUrlProvider` returns `signed://derived/...` with a short expiry and no host. Search asks for a URL only after the policy allows it. The workbook export also calls `sign_derived`, then `SignedDerivedFrameSource` drops the locator and reads bytes from a `FrameByteReader`. Original object URLs are not stored and not returned. The workbook never contains a URL or a hyperlink.

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

## Query workbook export

`GET /api/v1/queries/{query_id}/export` writes the caller's view of one saved query. Missing `X-User-Id` is a free caller. An unknown user id is 404. A missing query id is 404.

Only a live `regular` (普通会员) or `vip` grant can export. Free callers, and callers whose only live grants are `program_90`, `program_30`, or `screenshot`, get 403. Expired grants do not count. There is no row cap. If both `regular` and `vip` are live, VIP wins and the wider age floor is used.

A row is one index record (one review video hitting one program) that this member is allowed to see. `EntitlementPolicy` must unlock it (`reveal_fields`). The same platform and program can appear on several rows, each with its own 检测日期 and screenshot. Records left out, matching search:

- task status other than `done` (a hit whose task row is missing still counts as `done`)
- `is_test`, or a filename or platform that starts with `test_` after the same case-insensitive fold
- no detection time (`检测时间待核实`)
- age under 30 days for every tier
- age 30–89 for 普通

Locked rows are omitted. They are not filled with blanks that would still leak a platform or a time. A visible row whose derived frame is missing shows `暂无截图` and no picture.

### Sheet 「命中结果」

Header block, then the columns. There is no 客户 field.

| Label | Value |
|---|---|
| title | `那豆在架 命中结果` |
| 查询编号 | query id |
| 查询范围 | `{n} 个节目 · 已收录平台 · ≥30 天记录` for VIP, `≥90 天记录` for 普通. `n` is the number of programs on the query. |
| 生成日期 | Beijing calendar date `YYYY-MM-DD` of the export clock |

Columns, in order: 平台, 子类型, 节目类型, 上传节目名, 匹配节目名, 检测日期, 命中画面截图.

There is no 匹配方式 column, no yellow alias fill, and no 「名称相近，请核对」 note. An `alias` resolution is a confirmed hit through the admin alias table, and it uses the same columns as `exact` or `manual`. The only hit label is `确认命中`. Every exported row would show that same value, so the 命中类型 column is omitted.

平台 is the province+operator name from `PlatformNames`, such as `山东移动` or `示例视频`. 子类型 is the network and subtype tags joined by a space, such as `专网 OTT`. A name that cannot be parsed is still a hit row: 平台 is `平台未识别` and 子类型 is blank.

节目类型 is the query program's type, or blank. 上传节目名 is the uploaded text. 匹配节目名 is the canonical name. 检测日期 is the Beijing calendar date of the same timestamp search uses (`tasks.created_at`, else `tasks.file_uploaded_at`, else `index_records.uploaded_at`). Date only, not a time.

A `dispute` record is unconfirmed and is not a hit row. A `pending` program is not a hit row either. If a confirmed program has no remaining visible consensus record, it is 未命中 on the second sheet.

### Screenshots

The cell embeds JPEG bytes with openpyxl. Nothing in the package is a hyperlink or a URL, including the OOXML parts outside the schema namespaces. `SignedDerivedFrameSource.fetch_representative` calls `sign_derived` for the thumbnail variant the policy already chose (`card` for a membership that can see the row), refuses a locator that is not `signed://derived/`, and then reads bytes from a `FrameByteReader`. The locator is discarded. `GeneratedFrameReader` supplies synthetic frames for tests and local runs. A private reader can replace it; it must not persist a public URL.

`FrameImage` scales the frame to 600 px wide, burns `那豆在架` into the pixels, and encodes a JPEG targeting about 50 KB. The sheet shows that JPEG scaled down so the row stays readable; the stored bytes stay at 600 px. Each sheet is set to print on one landscape tabloid page so the columns stay together.

### Sheet 「未命中与待处理」

Columns: 上传节目名, 状态, 说明. Row order follows the query. This sheet is the member's results summary: the count of each status is the count a results page would show for this caller.

| Resolution | 状态 | 说明 |
|---|---|---|
| `miss`, or `exact` / `alias` / `manual` with no visible record | 未命中 | stored detail, usually blank |
| `pending` | 待确认 | candidate names |
| `failed` | 失败 | failure reason |
An uploaded name is resolved with `ProgramNormalizer.canonical`, the same rule as search. Several alias targets are not collapsed into one of them and are not a separate outcome: the folded title is the program, and only records stored under that title are hits.

A program with at least one visible confirmed hit is not repeated here. Pending programs, failed programs, and unconfirmed `dispute` records do not contribute hit rows.

### Size split

`HitWorkbook` saves the xlsx and measures those bytes while it packs rows. The limit is 20 MiB (`ExportLimits.MAX_BYTES`). When the next record would cross the limit, the next rows go into `汇总-2.xlsx`, then `汇总-3.xlsx`, and so on. Every part repeats the full header block. When there is more than one part, the header also has `第 n 部分 / 共 N 部分`. 「未命中与待处理」 is written only on the last part. One record is never split; that part may stand alone over the limit only when a single row is already larger than the limit. The sum of hit rows across parts is the member's permitted record count.

One part is the response body `汇总.xlsx` (`application/vnd.openxmlformats-officedocument.spreadsheetml.sheet`). Several parts are one `汇总.zip` of those files, not a JSON manifest, so the member downloads a single artifact. This zip is the split workbook only. It is not an evidence package and not a bulk screenshot download. There is still no download-archive table.
