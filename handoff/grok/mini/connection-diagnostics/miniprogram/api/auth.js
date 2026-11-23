var state = require('./state')
var request = require('./request')
var diag = require('./device-diagnostics')
var log = require('./native-log')
var pending = null

function login() {
  var revision = state.sessionRevision()
  if (pending && pending.revision === revision) return pending.promise
  var exchangeStatus = 0
  var marker = log.current()
  var attempt = { revision: revision }
  pending = attempt
  function checkCurrent() {
    if (revision === state.sessionRevision()) return
    var error = new Error('账户已改变')
    error.sessionChanged = true
    throw error
  }
  attempt.promise = new Promise(function (resolve, reject) {
    try { wx.login({
      timeout: 10000,
      success: function (result) {
        log.record(marker, '微信取码', 'success', result)
        if (!result || !result.code) { reject(diag.error('code', '微信取码')); return }
        resolve(result.code)
      },
      fail: function (error) { log.record(marker, '微信取码', 'fail', error); reject(diag.network(error, '微信取码')) }
    }) } catch (error) { log.record(marker, '微信取码', 'throw', error); reject(diag.network(error, '微信取码')) }
  }).then(function (code) {
    checkCurrent()
    return request('/api/v1/auth/wechat/login', { code: code }, { method: 'POST', public: true, observeStatus: function (value) { exchangeStatus = value } })
  }).then(function (body) {
    checkCurrent()
    if (!body || typeof body.token !== 'string' || !body.token || body.token_type !== 'bearer' ||
        !body.user || !body.user.id || !(Date.parse(body.expires_at) > Date.now())) {
      throw diag.error('sessionReply', '换码', exchangeStatus)
    }
    state.setSession(body, true)
    return body.user
  }).then(function (user) {
    if (pending === attempt) pending = null
    return user
  }, function (error) {
    if (pending === attempt) pending = null
    checkCurrent()
    throw error
  })
  return attempt.promise
}

module.exports = { login: login }
