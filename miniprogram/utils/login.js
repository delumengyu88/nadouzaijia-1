// Shared by guest entry points. Stay on the same page and preserve the draft/selection.
module.exports = function (page, refresh) {
  if (page.data.loginLoading) return Promise.resolve(false)
  page.setData({ loginLoading: true, loginError: '' })
  return require('../api/index').login().then(function () {
    page.setData({ loginLoading: false, loginError: '', needLogin: false })
    refresh.call(page)
    return true
  }).catch(function () {
    page.setData({ loginLoading: false, loginError: '登录失败，请重试' })
    return false
  })
}
