var api = require('../../api/index')
var nav = require('../../utils/nav')

Page({
  data: { title: '兑换邀请码', showBack: true, phase: 'form', code: '' },
  onLoad: function (query) {
    var layout = nav.layout('invite', this.query)
    var state = api.inviteState()
    this.setData({
      statusBarHeight: layout.statusBarHeight,
      navHeight: layout.navHeight,
      showBack: true,
      title: '兑换邀请码',
      phase: state.bound ? 'bound' : 'form',
      code: '',
      message: state.bound ? '已绑定过邀请码，不能再填' : ''
    })
    if (query.view === 'rate') {
      this.setData({ phase: 'rate', code: 'A8K3N2P7', message: '操作太频繁，请 1 小时后再试。' })
    }
  },
  back: function () {
    if (getCurrentPages().length > 1) wx.navigateBack()
    else wx.switchTab({ url: '/pages/profile/index' })
  },
  onCode: function (e) { this.setData({ code: e.detail.value }) },
  showResult: function (result, code) {
    if (result && result.ok) {
      this.setData({ phase: 'success', headline: result.headline, sub: result.sub, hasVip: result.hasVip })
      return
    }
    var kind = result && result.code
    var phase = kind === 'bound' ? 'bound' : (kind === 'limited' ? 'rate' : 'error')
    this.setData({
      phase: phase,
      message: (result && result.message) || '网络异常，请重试',
      code: code
    })
  },
  submit: function () {
    if (this._busy || this.data.phase === 'bound' || this.data.phase === 'rate' || this.data.phase === 'redeeming') return
    var code = this.data.code
    var self = this
    if (require('../../config').mode === 'live') {
      this._busy = true
      this.setData({ phase: 'redeeming', code: code })
      Promise.resolve().then(function () { return api.redeem(code) }).then(function (result) {
        self._busy = false
        self.showResult(result, code)
      }, function () {
        self._busy = false
        self.setData({ phase: 'error', message: '网络异常，请重试', code: code })
      })
      return
    }
    this.setData({ phase: 'redeeming', code: code })
    nav.later(500, function () {
      self.showResult(api.redeem(code), code)
    })
  },
  reset: function () { this.setData({ phase: 'form', message: '', code: '' }) },
  profile: function () { wx.switchTab({ url: '/pages/profile/index' }) }
})
