var api = require('./api/index')
var privacy = require('./utils/privacy')

App({
  onLaunch: function () {
    api.bootstrap()
    if (typeof wx.onNeedPrivacyAuthorization === 'function') {
      wx.onNeedPrivacyAuthorization(function (resolve) {
        privacy.notify(resolve)
      })
    }
  }
})
