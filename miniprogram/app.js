var splash = require('./api/splash')
var api = require('./api/index')
var privacy = require('./utils/privacy')

App({
  globalData: {
    splashShownThisLaunch: false
  },
  onLaunch: function () {
    api.bootstrap()
    this.identify()
    splash.cold(this.globalData)
    if (typeof wx.onNeedPrivacyAuthorization === 'function') {
      wx.onNeedPrivacyAuthorization(function (resolve) {
        privacy.notify(resolve)
      })
    }
  },
  onHide: function () { splash.background() },
  onShow: function (options) {
    splash.foreground(this.globalData, options)
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
