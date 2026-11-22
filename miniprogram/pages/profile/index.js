var api = require('../../api/index')
var nav = require('../../utils/nav')

function readProfile() {
  if (require('../../config').mode === 'mock') return api.profile()
  return require('../../api/http').profile()
}

function showProfile(info) {
  if (!info || info.guest || info.heading) return info
  var names = { vip: 'VIP', regular: '普通会员', free: '免费用户' }
  var heading = names[info.tier] || '会员信息'
  var detail = ''
  if (info.tier === 'free') detail = '免费 · 可看 180 天以上低清记录'
  else if (info.title && info.title.indexOf('至 ') !== -1) detail = info.title.slice(info.title.indexOf('至 '))
  else detail = '\u2014'
  return Object.assign({}, info, { heading: heading, detail: detail })
}

Page({
  data: {
    useMock: require('../../config').mode === 'mock',
    title: '我的',
    showSheet: false,
    loading: require('../../config').mode !== 'mock',
    loadError: false,
    guest: false,
    nickname: '',
    lines: []
  },
  onShow: function () {
    var data = nav.layout('profile', this.query)
    var info = readProfile()
    var self = this
    this._seq = (this._seq || 0) + 1
    var seq = this._seq
    if (info && typeof info.then === 'function') {
      data.title = '我的'
      data.showSheet = false
      data.useMock = false
      data.loading = true
      data.loadError = false
      data.guest = false
      data.nickname = ''
      data.badge = ''
      data.lines = []
      data.bindText = ''
      this.setData(data)
      nav.selectTab(this, 2)
      info.then(function (profile) {
        if (seq !== self._seq) return
        var next = nav.layout('profile', self.query)
        next.showSheet = false
        next.useMock = false
        next.loading = false
        next.loadError = false
        profile = showProfile(profile)
        Object.keys(profile).forEach(function (key) { next[key] = profile[key] })
        next.title = '我的'
        self.setData(next)
      }, function () {
        if (seq !== self._seq) return
        self.setData({
          loading: false,
          loadError: true,
          guest: false,
          nickname: '',
          badge: '',
          lines: [],
          bindText: '兑换邀请码',
          bound: false
        })
      })
      return
    }
    data.title = '我的'
    data.showSheet = false
    data.useMock = require('../../config').mode === 'mock'
    data.loading = false
    data.loadError = false
    info = showProfile(info)
    Object.keys(info).forEach(function (key) { data[key] = info[key] })
    data.title = '我的'
    this.setData(data)
    nav.selectTab(this, 2)
  },
  retryLoad: function () { this.onShow() },
  login: function () {
    return require('../../utils/login')(this, this.onShow)
  },
  go: function (e) {
    wx.navigateTo({ url: e.currentTarget.dataset.url })
  },
  noop: function () {},
  openSheet: function () { this.setData({ showSheet: true }) },
  closeSheet: function () { this.setData({ showSheet: false }) },
  useAccount: function (e) {
    api.switchAccount(e.currentTarget.dataset.id)
    this.onShow()
  }
})
