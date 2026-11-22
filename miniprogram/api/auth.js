var state = require('./state')
var request = require('./request')
var pending = null

function login() {
  if (pending) return pending
  pending = new Promise(function (resolve, reject) {
    wx.login({
      timeout: 10000,
      success: function (result) {
        if (!result.code) { reject(new Error('登录失败，请重试')); return }
        resolve(result.code)
      },
      fail: function () { reject(new Error('登录失败，请重试')) }
    })
  }).then(function (code) {
    return request('/api/v1/auth/wechat/login', { code: code }, { method: 'POST', public: true })
  }).then(function (body) {
    if (typeof body.token !== 'string' || !body.token || body.token_type !== 'bearer' ||
        !body.user || !body.user.id || !(Date.parse(body.expires_at) > Date.now())) {
      throw new Error('登录失败，请重试')
    }
    state.setSession(body)
    return body.user
  }).then(function (user) { pending = null; return user }, function (error) { pending = null; throw error })
  return pending
}

module.exports = { login: login }
