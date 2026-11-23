# 小程序交接

查询结果锁卡和付款入口已提交到 [PR #67](https://github.com/delumengyu88/nadouzaijia-1/pull/67)，提交 `f2239b9`。现有权限可打开文字摘要，单次解锁等待定价。此次快照保存两套此前只留在本机的小程序诊断源码。

- `connection-diagnostics` 可在手机本地写入完整微信错误原文，并从页面复制或导出。已从真机读到连接被重置的错误。
- `public-health-diagnostics` 仅检查公开健康接口。本机验证了成功和失败回调，待 iPhone 对照复测。

这两套工程用于单独诊断。导入前从私有配置取得正式小程序 ID 和 HTTPS 服务地址。完整诊断在本地复制 `miniprogram/config.local.example.js` 为 `miniprogram/config.local.js`，填入 `apiBase` 和 `expectedAppId`。健康诊断在本地填写 `miniprogram/endpoint.js` 所需的健康检查地址。项目配置中使用占位 ID，正式值只填在本机。所有正式配置和采集日志均留在原处。

普通体验版 0.1.1127 对应 `7f509fd`。iPhone 在会员中心提示网络异常，相关请求未被正式入口记录，真实交易待测。
