var api = require('./api/index')

App({
  globalData: {
    splashShownThisLaunch: false
  },
  onLaunch: function () {
    api.bootstrap()
    this.globalData.splashShownThisLaunch = false
  }
})
