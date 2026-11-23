var api = require('./api/index')
var privacy = require('./utils/privacy')

App({
  globalData: {
    splashShownThisLaunch: false
  },
  onLaunch: function () {
    api.bootstrap()
    this.globalData.splashShownThisLaunch = false
    if (typeof wx.onNeedPrivacyAuthorization === 'function') {
      wx.onNeedPrivacyAuthorization(function (resolve) {
        privacy.notify(resolve)
      })
    }
  },
  onShow: function () {
    // Expire a cached session before the visible page reads its private data.
    require('./api/state').queryToken()
  }
})
