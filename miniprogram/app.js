var api = require('./api/index')

App({
  globalData: {
    splashShownThisLaunch: false
  },
  onLaunch: function () {
    api.bootstrap()
    this.globalData.splashShownThisLaunch = false
  },
  onShow: function () {
    // 从后台或分享卡片回到已在运行的进程。不清开屏标记，避免热启动再弹。
  }
})
