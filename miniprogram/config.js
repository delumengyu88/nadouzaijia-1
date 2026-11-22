// 微信登录始终走 API；mock: 业务模拟；hybrid: 检索/报告接 API；live: HTTP 接入。
// apiBase 仅在本机配置，仓库保持空占位；真实请求统一使用微信登录 session。
module.exports = {
  mode: 'hybrid',
  trialMode: true,
  calibrating: true, // 数据校准期间仍提交查询，隐藏结果并停用支付和报告。
  apiBase: ''
}
