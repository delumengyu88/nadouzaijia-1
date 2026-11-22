var api = require('./api/index')
var privacy = require('./utils/privacy')

App({
  globalData: {
    splashShownThisLaunch: false
  },
  onLaunch: function () {
    api.bootstrap()
    this.identify()
    this.globalData.splashShownThisLaunch = false
    if (typeof wx.onNeedPrivacyAuthorization === 'function') {
      wx.onNeedPrivacyAuthorization(function (resolve) {
        privacy.notify(resolve)
      })
    }
  },
  onShow: function () {
    this.identify()
  },
  identify: function () {
    if (api.accountKind() !== 'guest' || this.identifying) return
    var app = this
    this.identifying = api.ensureEntry().then(function (user) {
      if (user && user.id && user.id !== require('./api/state').userId()) return
      api.entryFailed = false
      var pages = getCurrentPages()
      var page = pages[pages.length - 1]
      if (page && page.onIdentityReady) page.onIdentityReady()
    }, function (error) {
      if (error.sessionChanged) return
      api.entryFailed = true
      var pages = getCurrentPages()
      var page = pages[pages.length - 1]
      if (page && page.onIdentityError) page.onIdentityError()
    }).then(function () { app.identifying = null }, function () { app.identifying = null })
  }
})
