// mock: 全部模拟；hybrid: 检索/报告使用 API，其余模拟；live: HTTP 接入。
// apiBase 仅在本机配置，仓库保持空占位；测试身份须与后端已有用户 ID 对应。
module.exports = {
  mode: 'hybrid',
  trialMode: true,
  calibrating: false, // 数据校准期间仍提交查询，隐藏结果并停用支付和报告。
  apiBase: '',
  testUserIds: { free: 'u-free', member: 'u-member' }
}
