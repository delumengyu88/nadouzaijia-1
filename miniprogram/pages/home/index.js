var api = require('../../api/index')
var nav = require('../../utils/nav')
var splash = require('../../api/splash')
var layoutSplash = require('./splash-layout').layoutSplash
var config = require('../../config')
var httpApi = require('../../api/http')

var seenAnnounce = Object.create(null)
var MOCK_ANNOUNCE = {
  title: '公告',
  body: '每月 2 日和 16 日更新片单索引。免费用户可看 ≥180 天低清记录。',
  buttonText: '我知道了'
}

function mockMode() {
  return (config.mode || 'mock') === 'mock'
}

function announceKey(item) {
  return String(item.id || '') + '\n' + String(item.title || '') + '\n' + String(item.body || '')
}

function splashFrame() {
  var sys = {}
  var menu = null
  try { sys = wx.getSystemInfoSync() || {} } catch (err) {}
  try {
    if (wx.getMenuButtonBoundingClientRect) menu = wx.getMenuButtonBoundingClientRect()
  } catch (err) {}
  return layoutSplash(sys, menu)
}

Page({
  data: { showBrand: true, title: '', examples: [], taps: 0, showSplash: false, seconds: 5, splashFailed: false, showPlatformTotal: false, announcement: null },
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
    if (!mockMode()) this.loadHome()
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
      var frame = splashFrame()
      Object.keys(frame).forEach(function (key) { data[key] = frame[key] })
    }
    if (mockMode()) {
      data.showPlatformTotal = typeof home.platformTotal === 'number' && home.platformTotal > 0
      data.announcement = home.showAnnounce ? MOCK_ANNOUNCE : null
    } else {
      var pub = this._public
      data.examplesError = false
      data.announcement = null
      data.showAnnounce = false
      if (!pub) {
        data.examples = []
        data.showPlatformTotal = false
        data.platformTotal = ''
      } else {
        data.examples = pub.examples || []
        data.showPlatformTotal = !!pub.showPlatformTotal
        data.platformTotal = pub.showPlatformTotal ? pub.platformTotal : ''
        var first = (pub.announcements || [])[0]
        if (first && !seenAnnounce[announceKey(first)]) {
          data.announcement = first
          data.showAnnounce = true
        }
      }
    }
    this.setData(data)
    nav.selectTab(this, 0)
  },
  loadHome: function () {
    var self = this
    var seq = (this._homeSeq || 0) + 1
    this._homeSeq = seq
    httpApi.homeContent().then(function (content) {
      if (self._homeSeq !== seq) return
      var cards = content.examples || []
      if (!cards.length) {
        self._public = content
        self.paint()
        return
      }
      var factsOf = require('../../utils/present').exampleFacts
      Promise.all(cards.map(function (card) {
        return httpApi.exampleReport(card.name).then(function (report) {
          var facts = factsOf(report && report.records)
          if (!facts.hit) return null
          return { name: card.name, line: facts.line, sub: facts.sub, image: facts.image, illustrated: false }
        }, function () { return null })
      })).then(function (ready) {
        if (self._homeSeq !== seq) return
        content.examples = ready.filter(Boolean)
        self._public = content
        self.paint()
      })
    }, function () {
      if (self._homeSeq !== seq) return
      self._public = { examples: [], announcements: [], showPlatformTotal: false, platformTotal: null }
      self.paint()
    })
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
    var frame = splashFrame()
    frame.showSplash = true
    frame.seconds = 5
    this.setData(frame)
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
  // 图片失败只露出同色调深色底，倒计时继续，不写存储。
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
    if (mockMode()) {
      api.markAnnounceSeen()
      this.onShow()
      return
    }
    if (this.data.announcement) seenAnnounce[announceKey(this.data.announcement)] = true
    this.setData({ announcement: null, showAnnounce: false })
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
