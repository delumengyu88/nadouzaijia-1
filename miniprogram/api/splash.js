var state = require('./state')
var request = require('./request')

var CONFIG_PATH = '/api/v1/app/config'
var CONFIG_TIMEOUT = 1500

function seen() {
  return !!state.load().splashSeen
}

function markSeen() {
  var session = state.load()
  session.splashSeen = true
  state.save()
}

function launchData() {
  var app = getApp()
  if (!app.globalData) app.globalData = {}
  return app.globalData
}

// 只存在这次进程里。冷启动时 App.onLaunch 会清掉，不写本地存储。
function shownThisLaunch() {
  return !!launchData().splashShownThisLaunch
}

function markShownThisLaunch() {
  launchData().splashShownThisLaunch = true
}

// 开屏要不要出现，只看这里：后台明确关闭才不展示，其余都展示。
// 同一次冷启动里已经展示过，由 App 内存标记另外判断。
function shouldShow(enabled) {
  return enabled !== false
}

function readEnabled(body) {
  if (!body || typeof body !== 'object') return true
  return body.splash_enabled === false ? false : true
}

// 接口未上线、失败、超时或字段缺失都当作开启。只有明确的 false 才关闭。
// mock 不请求网络，直接开启。调用方应先把开屏画出来，再根据结果决定要不要收起。
function loadEnabled() {
  if (require('../config').mode === 'mock') return Promise.resolve(true)
  return new Promise(function (resolve) {
    var settled = false
    function finish(value) {
      if (settled) return
      settled = true
      resolve(value)
    }
    var timer = setTimeout(function () { finish(true) }, CONFIG_TIMEOUT)
    request(CONFIG_PATH, {}, { public: true, timeout: CONFIG_TIMEOUT }).then(function (body) {
      clearTimeout(timer)
      finish(readEnabled(body))
    }, function () {
      clearTimeout(timer)
      finish(true)
    })
  })
}

module.exports = {
  seen: seen,
  markSeen: markSeen,
  shownThisLaunch: shownThisLaunch,
  markShownThisLaunch: markShownThisLaunch,
  shouldShow: shouldShow,
  loadEnabled: loadEnabled,
  readEnabled: readEnabled
}
