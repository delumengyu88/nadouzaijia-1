var api = require('../../api/index')
var nav = require('../../utils/nav')

function inviteMask(text) {
  var value = String(text || '')
  var prefix = '邀请码 '
  return value.indexOf(prefix) === 0 ? value.slice(prefix.length) : ''
}

Page({
  data: { useMock: require('../../config').mode !== 'live', title: '我的', showSheet: false },
  onShow: function () {
    var data = nav.layout('profile', this.query)
    var info = api.profile()
    var live = require('../../config').mode === 'live'
    data.title = '我的'
    data.showSheet = false
    data.useMock = require('../../config').mode === 'mock'
    Object.keys(info).forEach(function (key) { data[key] = info[key] })
    data.inviteMask = inviteMask(info.bindText)
    if (live) {
      data.bindText = ''
      data.inviteError = ''
      data.inviteLoading = true
    }
    this.setData(data)
    nav.selectTab(this, 2)
    if (!live) return
    var self = this
    var ticket = (this._inviteTicket = (this._inviteTicket || 0) + 1)
    Promise.resolve().then(function () { return api.inviteState() }).then(function (state) {
      if (ticket !== self._inviteTicket) return
      self.showInvite(state)
    }, function () {
      if (ticket !== self._inviteTicket) return
      self.showInvite({ ok: false, message: '网络异常，请重试' })
    })
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
