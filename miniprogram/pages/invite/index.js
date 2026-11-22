var api = require('../../api/index')
var nav = require('../../utils/nav')

Page({
  data: { title: '兑换邀请码', showBack: true, phase: 'form', code: '' },
  onLoad: function (query) {
    var layout = nav.layout('invite', this.query)
    var state = api.inviteState()
    this.setData({
      statusBarHeight: layout.statusBarHeight,
      navHeight: layout.navHeight, trialData: layout.trialData,
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
  submit: function () {
    if (this.data.phase === 'bound' || this.data.phase === 'rate') return
    var code = this.data.code
    this.setData({ phase: 'redeeming', code: code })
    var self = this
    nav.later(500, function () {
      var result = api.redeem(code)
      if (result.ok) {
        self.setData({ phase: 'success', headline: result.headline, sub: result.sub, hasVip: result.hasVip })
        return
      }
      var phase = result.code === 'bound' ? 'bound' : (result.code === 'limited' ? 'rate' : 'error')
      self.setData({ phase: phase, message: result.message, code: code })
    })
  },
  reset: function () { this.setData({ phase: 'form', message: '', code: '' }) },
  profile: function () { wx.switchTab({ url: '/pages/profile/index' }) }
})
