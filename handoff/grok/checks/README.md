# 测试与合并交接

[PR #67](https://github.com/delumengyu88/nadouzaijia-1/pull/67) 的后端阶段是 `b09f353`。最终前后端提交 `f2239b9`，远端六项通过，已分别跑后端专项和小程序检查。完整的最终版本本机六项未留一套结果。

[PR #68](https://github.com/delumengyu88/nadouzaijia-1/pull/68) 的 `c8af185` 本机及远端六项均通过。两组各 13 条历史数据通过 0019 与 0020 往返，顺序和开关保留。缺少匿名授权时，公开首页为 0，详情为 404。非空授权数据会阻止有损降级。

[PR #69](https://github.com/delumengyu88/nadouzaijia-1/pull/69) 的 `672656f` 只改 README 和支付说明，远端六项通过。

测试代理的仓库工作区没有未提交的源码。隔离日志和本机测试辅助脚本留在原处。以上候选均未在本次交接期间合并。
