# 那豆在架 backend skeleton

This PR is the smallest backend later PRs can build on: import the hourly task export, store the index, and answer a program-name search with server-side entitlement filtering. The mini program, virtual payment, and the support workbench are out of scope.

## Out of scope

- WeChat login, virtual payment, orders, invite codes, and the support workbench.
- Excel hit-list parsing, the hourly COS scan, thumbnail rendering, and the private bucket / CDN.
- **ZIP / bulk download.** Evidence ZIPs are an offline 委托取证 deliverable only. Member batch ZIP (called out in the v1.1 spec as a later idea) is removed. This PR adds no ZIP endpoint, no download-archive table, and no bulk-export model. Per-record download *flags* exist so the permission matrix can say whether a member may download a report or a screenshot later; nothing in this PR returns a file or a ZIP.

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
| `platform_aliases` | Unused by search. Platform names come from `PlatformNames` in code, not from this table and not from an admin screen. |
| `program_aliases` | Same fold-and-map rules for indexed names and the search query. |
| `users` | Caller of the search endpoint. `X-User-Id` is a local-only stand-in until WeChat login; it is not production authentication. |
| `entitlements` | Active grants: `regular` / `vip` (expiry), `program_90` / `program_30` (canonical program + expiry), `screenshot` (one record id + expiry). The policy takes the widest live grant. Payment upgrade rules (suspended 普通 days, 7-day renewals) are not implemented; a grant counts when `expires_at` is in the future. |

`PlatformNames` maps a variant such as `GD移动` to one standard name, `广东移动`. The same class is used for the CSV `平台名` and for the filename parser. A value that maps to zero standards, or to more than one, is unrecognized: the row is still a hit, the visible platform is `平台未识别`, and it is not included in `platform_count`. The map never guesses a platform. `tasks.platform_name` is null and `source_unverified` is true in that case. `is_test` is set when the raw filename, the raw platform, or the canonical platform starts with `test_` (case-insensitive, after full/half-width fold). Test rows are stored so a later export can clear the flag, and they never appear in search.

No other tables. In particular there is no order table and no ZIP / archive table.

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
- The chosen platform string then goes through `PlatformNames` (trim, full/half-width, casefold, then the code map). Zero or several standard names → `平台未识别`.
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

`platform_count` is distinct recognized platforms on counted records with a known age `≥30`. `平台未识别` rows are hits but are not part of that count; `unverified_source_count` is how many of those aged rows are unrecognized. If the caller's program name matches more than one catalog program, the outcome is `ambiguous`, the message is `匹配到多个同名节目，补充年份或集数后重新查询`, and those rows are not hits: empty `records`, and `locked_summary` / pricing stay at zero. The API does not choose the closest program. Rows with no check time are in neither count. Outcome is `hit` when any dated record is `≥30`, `recent_only` when the only dated records are `<30`, otherwise `miss` (an unknown-age placeholder can still be listed). Cancelled, duplicate, and `test_` rows are omitted entirely.

`IndexRecordStore.upsert` stores the program name only after `ProgramNormalizer` (the same fold and alias map search uses), so a later importer can pass a raw or full-width name and still match a search. That match is a normal confirmed hit. The search response has no `match_type`, alias, or pending field, and it does not say the hit came from an alias.

Only `hit_type=consensus` is returned. A `dispute` row (疑似 / not yet confirmed) is not a user hit and is left out of counts. The words 待确认 and 疑似 are not sent to the client.

A locked row is an opaque placeholder: `record_id`, `age_bucket`, `locked`, `lock_reason`. It has no program name, platform, screenshot, or URL. The counts live only in `locked_summary`. The frontend draws the blurred card from that placeholder.

Several grants at once: visibility is the widest; report download is only when a membership tier itself covers that age; screenshot download is membership-or-that-screenshot.

## File access

`SignedUrlProvider.sign_derived(variant, record_id, user_id)` signs `lowres`, `card`, or `hd`. Any other variant raises. `FakeSignedUrlProvider` returns `signed://derived/...` with a short expiry and no host. The search handler is the only place that asks for a URL, and only after the policy allows it. Original object URLs are not stored and not returned.

## Local run

Postgres via `docker-compose.yml`, published only on `127.0.0.1:5432`. The API container runs Alembic then uvicorn. `python -m nadou.seed` writes a synthetic export (no real titles, buckets, domains, or hosts), imports it, and inserts matching index rows. It also creates a fixed VIP user id, so it refuses to run unless `NADOU_ALLOW_DEV_SEED=1`. That flag defaults off. Compose sets it on the local API container only.
