var api = require('../../api/index')
var nav = require('../../utils/nav')
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

Page(require('../../api/splash').page({
  data: { showBrand: true, title: '', examples: [], taps: 0, showPlatformTotal: false, announcement: null },
  onShow: function () {
    require('../../utils/login').onShow(this)
    if (api.entryFailed && api.accountKind() === 'guest') this.onIdentityError()
    this.paint()
    if (!mockMode()) this.loadHome()
  },
  paint: function () {
    var data = nav.layout('home', this.query)
    var home = api.homeState()
    data.identifying = !!(typeof getApp === 'function' && getApp().identifying)
    data.showBrand = true
    data.title = ''
    data.showBack = false
    Object.keys(home).forEach(function (key) { data[key] = home[key] })
    if (mockMode()) {
      data.showPlatformTotal = typeof home.platformTotal === 'number' && home.platformTotal > 0
      data.announcement = home.showAnnounce ? MOCK_ANNOUNCE : null
    } else {
      var pub = this._public
      data.examplesError = !!(pub && pub.examplesError)
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
      var failed = false
      var factsOf = require('../../utils/present').exampleFacts
      Promise.all(cards.map(function (card) {
        return httpApi.exampleReport(card.name).then(function (report) {
          var facts = factsOf(report && report.records)
          if (!facts.hit) return null
          return { name: card.name, line: facts.line, sub: facts.sub, image: facts.image, illustrated: false }
        }, function () { failed = true; return null })
      })).then(function (ready) {
        if (self._homeSeq !== seq) return
        content.examplesError = failed
        content.examples = ready.filter(Boolean)
        self._public = content
        self.paint()
      })
    }, function () {
      if (self._homeSeq !== seq) return
      self._public = { examplesError: true, examples: [], announcements: [], showPlatformTotal: false, platformTotal: null }
      self.paint()
    })
  },
  exampleImageFailed: function (event) {
    var data = event.currentTarget.dataset
    var cards = this._public && this._public.examples
    var card = cards && cards.find(function (item) { return item.name === data.name && item.image === data.src })
    if (!card) return
    card.image = ''
    this.setData({ examples: cards })
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
    api.setDraft(text)
    wx.navigateTo({ url: '/pages/input/index?auto=1' })
  },
  onIdentityReady: function () { this.setData({ loginError: '' }); this.paint() },
  onIdentityError: function () { this.setData({ identifying: false, loginError: '网络异常，可重试' }) },
  login: function () {
    return require('../../utils/login')(this, this.onShow)
  },
  upload: function () {
    var self = this
    require('../../api/splash').callNative(wx.chooseMessageFile, {
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
    if (!mockMode()) return
    var n = (this.data.taps || 0) + 1
    if (n >= 5) {
      api.toggleExamples()
      this.setData({ taps: 0 })
      this.onShow()
      return
    }
    this.setData({ taps: n })
  }
}))
