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
    // 从后台或分享卡片回到已在运行的进程。不清开屏标记，避免热启动再弹。
  }
})
