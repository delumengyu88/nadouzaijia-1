var api = require('../../api/index')
var nav = require('../../utils/nav')

Page({
  data: { showBrand: true, title: '', examples: [], taps: 0, showSplash: false, seconds: 5 },
  onShow: function () {
    this.paint()
    if (!api.splashSeen()) this.armSplash()
  },
  onUnload: function () {
    if (this.splashTimer) clearInterval(this.splashTimer)
    if (this.splashLoadTimer) clearTimeout(this.splashLoadTimer)
  },
  paint: function () {
    var data = nav.layout('home', this.query)
    var home = api.homeState()
    data.showBrand = true
    data.title = ''
    data.showBack = false
    data.showSplash = !api.splashSeen()
    data.seconds = this.data.seconds || 5
    Object.keys(home).forEach(function (key) { data[key] = home[key] })
    this.setData(data)
    nav.selectTab(this, 0)
  },
  armSplash: function () {
    if (this.splashTimer) return
    var self = this
    var sys = wx.getSystemInfoSync()
    var menu = wx.getMenuButtonBoundingClientRect && wx.getMenuButtonBoundingClientRect()
    var width = sys.windowWidth || 375
    this.setData({ showSplash: true, seconds: 5, splashFailed: false,
      splashSkipTop: menu && menu.top ? menu.top : (sys.statusBarHeight || 20) + 8,
      splashSkipRight: menu && menu.left ? width - menu.left + 8 : 102 })
    this.splashLoadTimer = setTimeout(function () { self.splashFailed() }, 1000)
    this.splashTimer = setInterval(function () {
      var next = self.data.seconds - 1
      if (next <= 0) {
        self.finishSplash()
        return
      }
      self.setData({ seconds: next })
    }, 1000)
  },
  splashLoaded: function () {
    if (this.splashLoadTimer) clearTimeout(this.splashLoadTimer)
    this.splashLoadTimer = 0
  },
  splashFailed: function () {
    this.setData({ splashFailed: true })
    this.finishSplash()
  },
  finishSplash: function () {
    if (this.splashTimer) clearInterval(this.splashTimer)
    if (this.splashLoadTimer) clearTimeout(this.splashLoadTimer)
    this.splashTimer = 0
    api.markSplashSeen()
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
