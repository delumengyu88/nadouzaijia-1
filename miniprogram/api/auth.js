var state = require('./state')
var request = require('./request')
var pending = null

function login() {
  var revision = state.sessionRevision()
  if (pending && pending.revision === revision) return pending.promise
  var attempt = { revision: revision }
  pending = attempt
  function checkCurrent() {
    if (revision === state.sessionRevision()) return
    var error = new Error('账户已改变')
    error.sessionChanged = true
    throw error
  }
  attempt.promise = new Promise(function (resolve, reject) {
    wx.login({
      timeout: 10000,
      success: function (result) {
        if (!result.code) { reject(new Error('登录失败，请重试')); return }
        resolve(result.code)
      },
      fail: function () { reject(new Error('登录失败，请重试')) }
    })
  }).then(function (code) {
    checkCurrent()
    return request('/api/v1/auth/wechat/login', { code: code }, { method: 'POST', public: true })
  }).then(function (body) {
    checkCurrent()
    if (!body || typeof body.token !== 'string' || !body.token || body.token_type !== 'bearer' ||
        !body.user || !body.user.id || !(Date.parse(body.expires_at) > Date.now())) {
      throw new Error('登录失败，请重试')
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
