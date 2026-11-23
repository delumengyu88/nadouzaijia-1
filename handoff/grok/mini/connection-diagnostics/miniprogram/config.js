// 微信登录始终走 API；mock: 业务模拟；hybrid: 检索/报告接 API；live: HTTP 接入。
// apiBase 仅在本机配置，仓库保持空占位；真实请求统一使用微信登录 session。
const base = {
  mode: 'live',
  trialMode: true,
  liveOneYuanTest: false, // 白名单现网一元测试构建单独开启。
  virtualPayment: false, // 虚拟支付启用后走独立下单与查单接口。
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

var config = Object.assign({}, base, local)
// 微信体验版和正式版均只读真实服务。开发者工具可以显式选择离线样例。
if (typeof wx !== 'undefined' && typeof wx.getAccountInfoSync === 'function') {
  try {
    var info = wx.getAccountInfoSync()
    var version = info && info.miniProgram && info.miniProgram.envVersion
    if (version !== 'develop') config.mode = 'live'
  } catch (err) { config.mode = 'live' }
}
module.exports = config
