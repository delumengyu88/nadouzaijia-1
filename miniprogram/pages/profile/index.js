var api = require('../../api/index')
var nav = require('../../utils/nav')

function inviteMask(text) {
  var value = String(text || '')
  var prefix = '邀请码 '
  return value.indexOf(prefix) === 0 ? value.slice(prefix.length) : ''
}

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
  else if (info.title && info.title.indexOf('至 ') !== -1) detail = info.title
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
    require('../../utils/login').onShow(this)
    if (api.entryFailed && api.accountKind() === 'guest') this.onIdentityError()
    var data = nav.layout('profile', this.query)
    data.identifying = !!(typeof getApp === 'function' && getApp().identifying)
    var info = readProfile()
    var self = this
    this._seq = (this._seq || 0) + 1
    var seq = this._seq
    if (info && typeof info.then === 'function') {
      data.title = '我的'
      data.showSheet = false
      data.useMock = false
      data.inviteLoading = require('../../config').mode === 'live'
      data.inviteError = ''
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
        if (require('../../config').mode === 'live' && !profile.guest) {
          var sent = require('../../api/state').token()
          api.inviteState().then(function (invite) {
            if (seq !== self._seq) return
            if (sent !== require('../../api/state').token()) self.onShow()
            else self.showInvite(invite)
          })
        }
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
    data.inviteMask = inviteMask(info.bindText)
    this.setData(data)
    nav.selectTab(this, 2)
  },
  showInvite: function (state) {
    if (!state || !state.ok) {
      this.setData({
        inviteLoading: false,
        bindText: '',
        inviteError: (state && state.message) || '网络异常，请重试',
        bound: true
      })
      return
    }
    if (state.guest) {
      this.setData({ inviteLoading: false, guest: true, bindText: state.bindText, bound: false, inviteError: '' })
      return
    }
    this.setData({
      inviteLoading: false,
      guest: false,
      bindText: state.bindText,
      inviteMask: inviteMask(state.bindText),
      bound: !!state.bound,
      inviteError: ''
    })
  },
  onIdentityReady: function () { this.setData({ loginError: '' }); this.onShow() },
  onIdentityError: function () { this.setData({ identifying: false, loginError: '网络异常，可重试' }) },
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
