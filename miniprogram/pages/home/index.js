var api = require('../../api/index')
var nav = require('../../utils/nav')
var splash = require('../../api/splash')

function skipMetrics() {
  var sys = {}
  var menu = null
  try { sys = wx.getSystemInfoSync() || {} } catch (err) {}
  try {
    if (wx.getMenuButtonBoundingClientRect) menu = wx.getMenuButtonBoundingClientRect()
  } catch (err) {}
  var width = sys.windowWidth || 375
  return {
    splashSkipTop: menu && menu.top ? menu.top : (sys.statusBarHeight || 20) + 8,
    splashSkipRight: menu && menu.left ? width - menu.left + 8 : 102
  }
}

Page({
  data: { showBrand: true, title: '', examples: [], taps: 0, showSplash: false, seconds: 5, splashFailed: false },
  splashVisible: function () {
    if (!splash.shouldShow(this.splashEnabled)) return false
    if (this._splashLive) return true
    return !splash.shownThisLaunch()
  },
  onShow: function () {
    var show = this.splashVisible()
    if (show) {
      splash.markShownThisLaunch()
      this._splashLive = true
      this.holdTabBar()
    }
    this.paint()
    if (show) {
      this.armSplash()
      this.watchSplashSwitch()
    } else if (this._tabHeld) {
      this.releaseTabBar()
    }
  },
  onUnload: function () {
    this.clearSplashTimers()
    if (this._tabHeld) this.releaseTabBar()
  },
  paint: function () {
    var data = nav.layout('home', this.query)
    var home = api.homeState()
    data.showBrand = true
    data.title = ''
    data.showBack = false
    Object.keys(home).forEach(function (key) { data[key] = home[key] })
    data.showSplash = this.splashVisible()
    data.seconds = this.data.seconds || 5
    data.splashFailed = !!this.data.splashFailed
    if (data.showSplash) {
      var metrics = skipMetrics()
      data.splashSkipTop = metrics.splashSkipTop
      data.splashSkipRight = metrics.splashSkipRight
    }
    this.setData(data)
    nav.selectTab(this, 0)
  },
  // 自定义 tabBar 画在页面之上，页内遮罩盖不住，开屏期间先藏起。
  holdTabBar: function () {
    this._tabHeld = true
    if (typeof wx !== 'undefined' && wx.hideTabBar) wx.hideTabBar({ animation: false, fail: function () {} })
  },
  releaseTabBar: function () {
    this._tabHeld = false
    if (typeof wx !== 'undefined' && wx.showTabBar) wx.showTabBar({ animation: false, fail: function () {} })
  },
  clearSplashTimers: function () {
    if (this.splashTimer) clearInterval(this.splashTimer)
    this.splashTimer = 0
    this._splashArmed = false
  },
  armSplash: function () {
    if (this._splashArmed || this._splashDone) return
    this._splashArmed = true
    var self = this
    var metrics = skipMetrics()
    this.setData({
      showSplash: true, seconds: 5,
      splashSkipTop: metrics.splashSkipTop, splashSkipRight: metrics.splashSkipRight
    })
    this.splashTimer = setInterval(function () { self.splashTick() }, 1000)
  },
  splashTick: function () {
    if (this._splashDone || !this.data.showSplash) return
    var next = this.data.seconds - 1
    if (next <= 0) {
      this.finishSplash()
      return
    }
    this.setData({ seconds: next })
  },
  watchSplashSwitch: function () {
    var self = this
    if (this._splashWatchStarted) return
    this._splashWatchStarted = true
    splash.loadEnabled().then(function (enabled) {
      if (enabled !== false || self._splashDone || !self.data.showSplash) return
      self.dropSplash()
    })
  },
  splashLoaded: function () {
    if (this._splashDone || !this.data.showSplash) return
    this.setData({ splashFailed: false })
  },
  // 图片失败只换成同底色文字版，倒计时继续，不记为看过。
  splashFailed: function () {
    if (this._splashDone || !this.data.showSplash) return
    this.setData({ splashFailed: true })
  },
  dropSplash: function () {
    this.splashEnabled = false
    this._splashLive = false
    this.clearSplashTimers()
    this.releaseTabBar()
    this.paint()
  },
  finishSplash: function () {
    if (this._splashDone || !this.data.showSplash) return
    this._splashDone = true
    this._splashLive = false
    this.clearSplashTimers()
    this.releaseTabBar()
    this.paint()
  },
  closeAnnounce: function () {
    api.markAnnounceSeen()
    this.onShow()
  },
  openDone: function () {
    var id = this.data.doneQueryId
    if (id) api.markQuerySeen(id)
    wx.navigateTo({ url: '/pages/query/index?id=' + id })
  },
  openRunning: function () {
    if (!this.data.runningId) return
    wx.navigateTo({ url: '/pages/query/index?id=' + this.data.runningId })
  },
  onInput: function (e) {
    this.setData({ draft: e.detail.value })
    api.setDraft(e.detail.value)
  },
  goInput: function () {
    wx.navigateTo({ url: '/pages/input/index' })
  },
  start: function () {
    var text = this.data.draft || ''
    if (!String(text).trim()) {
      wx.showToast({ title: '请先输入节目名', icon: 'none' })
      return
    }
    if (!api.profile || api.accountKind() === 'guest') {
      this.setData({ needLogin: true })
      return
    }
    api.setDraft(text)
    wx.navigateTo({ url: '/pages/input/index?auto=1' })
  },
  login: function () {
    return require('../../utils/login')(this, this.onShow)
  },
  upload: function () {
    var self = this
    wx.chooseMessageFile({
      count: 1,
      type: 'file',
      extension: ['xlsx'],
      success: function (res) {
        var file = res.tempFiles && res.tempFiles[0]
        if (!file) return
        api.setDraft('')
        wx.navigateTo({
          url: '/pages/input/index?file=1&name=' + encodeURIComponent(file.name) + '&size=' + file.size +
            '&path=' + encodeURIComponent(file.path || '')
        })
      }
    })
  },
  openExample: function (e) {
    var name = e.currentTarget.dataset.name
    wx.navigateTo({ url: '/pages/report/index?example=1&name=' + encodeURIComponent(name) })
  },
  tapExamples: function () {
    var n = (this.data.taps || 0) + 1
    if (n >= 5) {
      api.toggleExamples()
      this.setData({ taps: 0 })
      this.onShow()
      return
    }
    this.setData({ taps: n })
  }
})
