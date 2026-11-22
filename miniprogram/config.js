// mock: 全部模拟；hybrid: 检索/报告使用 API，其余模拟；live: HTTP 接入。
// apiBase 仅在本机配置，仓库保持空占位；测试身份须与后端已有用户 ID 对应。
module.exports = {
  mode: 'hybrid',
  apiBase: '',
  testUserIds: { free: 'u-free', member: 'u-member' }
}
