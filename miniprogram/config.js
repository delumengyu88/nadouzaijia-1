// 微信登录始终走 API；mock: 业务模拟；hybrid: 检索/报告接 API；live: HTTP 接入。
// 生产 API 根地址只写在 PRODUCTION_API_BASE。体验版和正式版忽略本机 apiBase。
// 真实请求统一使用微信登录 session。
var PRODUCTION_API_BASE = 'https://nadou.youroem.cn'

const base = {
  mode: 'live',
  trialMode: false, // 体验版走真实支付；为 true 时会员中心和报告页拦截付款。
  liveOneYuanTest: false, // 白名单现网一元测试构建单独开启。
  diamondPayment: true, // 仅开启服务端已上架的¥1/5 天体验会员（内部代码 membership_diamond）。
  virtualPayment: true, // 虚拟支付启用后走独立下单与查单接口。
  calibrating: false, // 数据校准期间仍提交查询，隐藏结果并停用支付和报告。
  apiBase: PRODUCTION_API_BASE
}

// 本机覆盖：复制 config.local.example.js 为 config.local.js（已在 .gitignore）。
// 开发版可以改 apiBase。体验版和正式版仍用上面的生产根地址。
let local = {}
try {
  local = require('./config.local.js')
} catch (e) {
  local = {}
}

function joinApi(root, path) {
  var base = String(root || '').replace(/\/$/, '')
  if (!base) return ''
  var suffix = path == null ? '' : String(path)
  if (suffix && suffix.charAt(0) !== '/') suffix = '/' + suffix
  return base + suffix
}

var config = Object.assign({}, base, local)
// 微信体验版和正式版均只读真实服务，并且只访问生产根地址。
// 开发者工具可以显式选择离线样例，也可以用 config.local.js 指向本机。
if (typeof wx !== 'undefined' && typeof wx.getAccountInfoSync === 'function') {
  try {
    var info = wx.getAccountInfoSync()
    var version = info && info.miniProgram && info.miniProgram.envVersion
    if (version !== 'develop') {
      config.mode = 'live'
      config.apiBase = PRODUCTION_API_BASE
    }
  } catch (err) {
    config.mode = 'live'
    config.apiBase = PRODUCTION_API_BASE
  }
}
config.apiUrl = function (path) {
  return joinApi(config.apiBase, path)
}
module.exports = config
