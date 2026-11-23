// Shared by guest entry points. Stay on the same page and preserve the draft/selection.
module.exports = function (page, refresh) {
  if (page.loginAttempt) return Promise.resolve(false)
  var attempt = page.loginAttempt = {}
  page.setData({ loginLoading: true, loginError: '' })
  var api = require('../api/index')
  var pending = require('../config').mode === 'mock' ? api.login() : api.ensureEntry()
  function visible() {
    if (!page.route || typeof getCurrentPages !== 'function') return true
    var stack = getCurrentPages()
    return stack[stack.length - 1] === page
  }
  function release() {
    if (page.loginAttempt !== attempt) return false
    page.loginAttempt = null
    if (!visible()) return false
    page.setData({ loginLoading: false })
    return true
  }
  return pending.then(function (user) {
    if (!release() || (user && user.id && user.id !== require('../api/state').userId())) return false
    page.setData({ loginLoading: false, loginError: '', needLogin: false })
    refresh.call(page)
    return true
  }).catch(function (error) {
    if (!release() || error.sessionChanged) return false
    page.setData({ loginLoading: false, loginError: '登录失败，请重试' })
    return false
  })
}

// A hidden tab can survive navigation. Restore its button when it becomes visible again.
module.exports.onShow = function (page) {
  if (page.data.loginLoading && !page.loginAttempt) page.setData({ loginLoading: false })
}
