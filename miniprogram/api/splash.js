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

// 开屏要不要出现，只看这里。
// 当前：后台没有明确关掉，并且这台设备还没看完一次。
// 以后若要改成每次启动都展示，只改这个函数。
function shouldShow(enabled) {
  if (enabled === false) return false
  return !seen()
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
  shouldShow: shouldShow,
  loadEnabled: loadEnabled,
  readEnabled: readEnabled
}
