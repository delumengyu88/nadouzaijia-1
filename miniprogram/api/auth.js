var state = require('./state')
var request = require('./request')
var pending = null
var guestPending = null

function changed() {
  var error = new Error('账户已改变')
  error.sessionChanged = true
  return error
}

function querySession() {
  if (state.queryToken()) return Promise.resolve(state.load().auth.user)
  if (pending) return Promise.reject(changed())
  var revision = state.sessionRevision()
  if (guestPending && guestPending.revision === revision) return guestPending.promise
  var previous = state.pendingGuestToken()
  var previousUser = previous && state.load().auth.user.id
  var attempt = { revision: revision }
  guestPending = attempt
  attempt.promise = request('/api/v1/auth/guest/session', {}, { method: 'POST', public: !previous, renewGuest: !!previous, exposeStatus: true }).then(function (response) {
    if (guestPending !== attempt || state.sessionRevision() !== revision) throw changed()
    if (response.statusCode === 401 && previous) {
      state.clearSession(previous)
      var expired = new Error('上次访客会话已失效')
      expired.statusCode = 401
      throw expired
    }
    if (response.statusCode !== 200) throw new Error('免费查询服务暂不可用')
    var body = response.data
    var user = body && body.user
    if (previous && (!user || user.id !== previousUser || body.token !== previous)) throw changed()
    if (!user || user.identity_kind !== 'guest' || user.tier !== 'free' || typeof user.id !== 'string' || !user.id.trim() ||
        user.vip_expires_at || user.regular_expires_at || !body.token || typeof body.token !== 'string' ||
        body.token_type !== 'bearer' || !(Date.parse(body.expires_at) > Date.now())) throw new Error('免费查询服务暂不可用')
    state.setSession(body, true)
    return user
  }).then(function (user) {
    if (guestPending === attempt) guestPending = null
    return user
  }, function (error) {
    var stale = guestPending !== attempt || state.sessionRevision() !== revision
    if (guestPending === attempt) guestPending = null
    if (stale && error.statusCode !== 401) throw changed()
    throw error
  })
  return attempt.promise
}

function login() {
  guestPending = null
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

module.exports = { login: login, querySession: querySession }
