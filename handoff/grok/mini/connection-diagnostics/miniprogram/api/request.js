var config = require('../config')
var state = require('./state')
var diag = require('./device-diagnostics')
var log = require('./native-log')

module.exports = function (path, data, options) {
  options = options || {}
  var base = String(config.apiBase || '').replace(/\/$/, '')
  if (!base) return Promise.reject(new Error('未配置服务地址'))
  var stages = { '/api/healthz':'健康', '/api/v1/auth/wechat/login':'换码', '/api/v1/auth/me':'本人资料' }
  var stage = stages[path.split('?')[0]]
  if (!stage) return Promise.reject(new Error('该诊断仅支持连接和登录'))
  var marker = log.current()
  var token = options.public ? '' : state.token()
  // optionalAuth sends a session when one exists, and still runs when it does not.
  // Invite state is 200 for a missing token. Other calls still require a session.
  if (!options.public && !options.optionalAuth && !token) return Promise.reject(new Error('请先登录'))
  return new Promise(function (resolve, reject) {
    // A synchronous throw must still settle the promise, or callers wait forever.
    try {
      wx.request({
        url: base + path,
        method: options.method || 'GET',
        timeout: options.timeout || 15000,
        data: data || {},
        header: token ? { Authorization: 'Bearer ' + token } : {},
        success: function (res) {
          log.record(marker, stage, 'success', res, res.statusCode)
          if (options.observeStatus) options.observeStatus(res.statusCode)
          // Evidence uses readBody and judges `code`. Redeem uses exposeStatus and judges `detail`.
          // Invite state uses optionalAuth. Keep one of each.
          if (options.readBody || options.exposeStatus) {
            if (res.statusCode === 401 && token) state.clearSession(token)
            if (options.exposeStatus) resolve({ statusCode: res.statusCode, data: res.data })
            else resolve(res.data === undefined || res.data === null ? {} : res.data)
            return
          }
          if (res.statusCode >= 200 && res.statusCode < 300) resolve(res.data || {})
          else {
            // An old in-flight request must not erase a newly logged-in account.
            if (res.statusCode === 401 && token) state.clearSession(token)
            reject(diag.error('http', path === '/api/v1/auth/wechat/login' ? '换码' : '', res.statusCode))
          }
        },
        // Keep this sentence identical to QueryGuard.networkText(); the results page
        // matches it to stop on the current progress and offer retry.
        fail: function (error) { log.record(marker, stage, 'fail', error); reject(diag.network(error, path === '/api/v1/auth/wechat/login' ? '换码' : '')) }
      })
    } catch (error) {
      log.record(marker, stage, 'throw', error)
      reject(diag.network(error, path === '/api/v1/auth/wechat/login' ? '换码' : ''))
    }
  })
}
