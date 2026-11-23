# 数据目录约定

月月已允许真实数据进入仓库、云端和 Codex；真实片库、共识表和其他业务数据必须集中放在本目录，不能写入代码、演示数据或测试夹具。测试继续使用合成假数据。

默认情况下，`data/` 内所有文件都被忽略。允许提交的例外仅为本说明，以及 `data/approved/` 第一层经过检查的 `.csv`、`.tsv`、`.xlsx` 数据文件。工作簿临时文件仍然忽略。不需要提交的数据放在 `data/` 的其他位置。当前不包含真实数据。

提交允许格式之前，检查所有单元格、隐藏工作表、链接、批注和文件元数据，确认不含密钥、桶名、IP 或域名。允许文件类型不等于文件内容已通过检查；`.gitignore` 无法检查内容。含这些内容的原始数据不能移动进 `approved/`，也不能通过强制添加绕过忽略规则。

不提交：密钥或凭据文件、环境配置、原始截图和视频、数据库备份、运行日志、临时导出、压缩包，以及未经检查的数据。真实 webhook 等配置只走环境变量。不得生成永久公开图片链接。

完整片库通过导入进入系统；检索命中数据只使用已校准的共识表，不把任务导出 CSV 当作共识节目来源。任务元数据和命中记录的导入路径需要分别核对。

`approved/` 是仓库文件管理约定，不新增后台审批流程或自动导入行为。历史数据表清理、生产导入范围和未经明确批准的规则仍由月月或团队决定。

## 已授权共识工作簿

对外查询使用 `APP_ENV=production`、`NADOU_DATA_SOURCE=export`、`NADOU_ALLOW_DEV_SEED=0`。来源清单由有权批准该工作簿用于本项目的人提供，每行用 `task_id,sha256` 对应一个已完成的项目任务和获准版本。仅计算文件摘要无法证明使用授权。

在获准导入的项目环境中执行 `python -m nadou import-approved-hitlist --manifest 清单.csv --workbook 共识表.xlsx --task-id 任务编号`。命令在同一事务内核对摘要、导入共识页并切换任务的有效版本。仅有任务元数据、待复核行、测试任务、生成的日期或失效版本均不能用作正式命中。没有可核验的命中时显示「数据待核验」。缺少经过授权的派生截图时，报告注明「暂无截图」。

### 单条人工复核摘要

`publish-reviewed-summary` 用于权利方明确同意展示的一条节目摘要。运维人员先逐项核对原始人工反馈、指定任务、节目、平台与帧数，在私有 JSON 清单保存 `scope=single_program_summary`、`review=pass`、`rights_holder`、`task_id`、`program`、`platform`、`frame_count`、`w2_sha256`、`tasks_sha256` 和 `feedback_sha256`。反馈摘要由运维人员对核验过的原文件计算，人工审核结论来自权利方及原反馈。校验只读 W2 快照和任务 CSV。

`python -m nadou publish-reviewed-summary --manifest 私有清单.json --w2 只读快照.db --task-csv 任务清单.csv --feedback-sha256 已实算的反馈摘要` 默认核验后回滚。加 `--apply` 仅更新对应既有索引行；生产须使用已备份验证的数据库与已审版本。授权留存清单、来源、反馈和行指纹，均不在用户接口返回。检索按既有权益分层，开放字段限于节目、命中数、平台、网络、日期、帧数。图片、视频、原报告和公开示例继续受限。

## W2 每日索引自动导入

大刀侧每天产出的 W2 版本由宿主机上的拉取器下载、校验并切换 `/var/lib/w2-index/current`，再由导入器执行 `python -m nadou w2-import --source <版本目录>` 写入 `index_records`。校验通过即上线，无需人工审批；导入在一个事务里完成，失败时继续使用上一版。导入的版本记录在 `w2_index_imports` 表，`python -m nadou w2-import --status` 或工作台 `/api/v1/admin/index/status` 的 `w2_index` 可查看。安装、回滚和故障演练见 `ops/w2-sync-puller/README.md`。
