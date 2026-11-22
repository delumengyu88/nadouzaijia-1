// 微信登录始终走 API；mock: 业务模拟；hybrid: 检索/报告接 API；live: HTTP 接入。
// apiBase 仅在本机配置，仓库保持空占位；真实请求统一使用微信登录 session。
const base = {
  mode: 'hybrid',
  trialMode: true,
  calibrating: true, // 数据校准期间仍提交查询，隐藏结果并停用支付和报告。
  apiBase: ''
}

// 本机覆盖：复制 config.local.example.js 为 config.local.js（已在 .gitignore），在里面填 apiBase 等本机值。
let local = {}
try {
  local = require('./config.local.js')
} catch (e) {
  local = {}
}

module.exports = Object.assign({}, base, local)
