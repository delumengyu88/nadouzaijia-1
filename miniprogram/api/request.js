var config = require('../config')
var state = require('./state')

module.exports = function (path, data, options) {
  options = options || {}
  var url = config.apiUrl(path)
  if (!url) return Promise.reject(new Error('未配置服务地址'))
  var token = options.public ? '' : state.token()
  // optionalAuth sends a session when one exists, and still runs when it does not.
  // Invite state is 200 for a missing token. Other calls still require a session.
  if (!options.public && !options.optionalAuth && !token) return Promise.reject(new Error('请先登录'))
  return new Promise(function (resolve, reject) {
    // A synchronous throw must still settle the promise, or callers wait forever.
    try {
      wx.request({
        url: url,
        method: options.method || 'GET',
        timeout: options.timeout || 15000,
        data: data || {},
        header: token ? { Authorization: 'Bearer ' + token } : {},
        success: function (res) {
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
            reject(new Error(res.statusCode === 401 ? '请先登录' : '服务暂不可用'))
          }
        },
        // Keep this sentence identical to QueryGuard.networkText(); the results page
        // matches it to stop on the current progress and offer retry.
        fail: function () { reject(new Error('网络异常，请重试')) }
      })
    } catch (error) {
      reject(new Error('网络异常，请重试'))
    }
  })
}
