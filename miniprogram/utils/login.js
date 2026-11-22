// Shared by guest entry points. Stay on the same page and preserve the draft/selection.
module.exports = function (page, refresh) {
  if (page.data.loginLoading) return Promise.resolve(false)
  page.setData({ loginLoading: true, loginError: '' })
  var api = require('../api/index')
  var pending = require('../config').mode === 'mock' ? api.login() : api.ensureEntry()
  function visible() {
    if (!page.route || typeof getCurrentPages !== 'function') return true
    var stack = getCurrentPages()
    return stack[stack.length - 1] === page
  }
  return pending.then(function (user) {
    if (!visible() || (user && user.id && user.id !== require('../api/state').userId())) return false
    page.setData({ loginLoading: false, loginError: '', needLogin: false })
    refresh.call(page)
    return true
  }).catch(function (error) {
    if (!visible() || error.sessionChanged) return false
    page.setData({ loginLoading: false, loginError: '登录失败，请重试' })
    return false
  })
}
