var api = require('../../api/index')
var nav = require('../../utils/nav')

Page({
  data: { title: '兑换邀请码', showBack: true, phase: 'loading', code: '', fieldError: false, loginLoading: false, loginError: '' },
  onLoad: function (query) {
    this._query = query || {}
    var layout = nav.layout('invite', this.query)
    var base = {
      statusBarHeight: layout.statusBarHeight,
      navHeight: layout.navHeight,
      showBack: true,
      title: '兑换邀请码',
      loginLoading: false,
      loginError: ''
    }
    if (require('../../config').mode !== 'live') {
      var state = api.inviteState()
      this.setData(Object.assign({
        phase: state.bound ? 'bound' : 'form',
        code: '',
        message: state.bound ? '已绑定过邀请码，不能再填' : ''
      }, base))
      if (this._query.view === 'rate') {
        this.setData({ phase: 'rate', code: 'A8K3N2P7', message: '操作太频繁，请 1 小时后再试。' })
      }
      return
    }
    this.setData(Object.assign({ phase: 'loading', code: '', message: '' }, base))
    var self = this
    var ticket = (this._inviteTicket = (this._inviteTicket || 0) + 1)
    Promise.resolve().then(function () { return api.inviteState() }).then(function (next) {
      if (ticket !== self._inviteTicket) return
      self.applyState(next)
    }, function () {
      if (ticket !== self._inviteTicket) return
      self.setData({ phase: 'unavailable', message: '网络异常，请重试', code: '' })
    })
  },
  applyState: function (state) {
    if (!state || !state.ok) {
      this.setData({
        phase: 'unavailable',
        message: (state && state.message) || '服务暂不可用',
        code: ''
      })
      return
    }
    if (state.guest) {
      this.setData({ phase: 'guest', code: '', message: '' })
      return
    }
    if (state.bound) {
      this.setData({ phase: 'bound', code: '', message: '已绑定过邀请码，不能再填' })
      return
    }
    this.setData({ phase: 'form', code: '', message: '' })
  },
  login: function () {
    var self = this
    return require('../../utils/login')(this, function () {
      self.onLoad(self._query || {})
    })
  },
  back: function () {
    if (getCurrentPages().length > 1) wx.navigateBack()
    else wx.switchTab({ url: '/pages/profile/index' })
  },
  onCode: function (e) { this.setData({ code: e.detail.value, fieldError: false }) },
  clearCode: function () { this.setData({ code: '', fieldError: false }) },
  showResult: function (result, code) {
    if (result && result.ok) {
      var expiry = result.expiry || ''
      this.setData({
        phase: 'success',
        headline: '兑换成功',
        sub: expiry ? ('有效至 ' + expiry) : '',
        hasVip: result.hasVip,
        fieldError: false
      })
      return
    }
    var kind = result && result.code
    var phase = kind === 'bound' ? 'bound' : (kind === 'limited' ? 'rate' : 'error')
    this.setData({
      phase: phase,
      message: (result && result.message) || '网络异常，请重试',
      code: code,
      fieldError: kind === 'invalid' || kind === 'expired' || kind === 'used'
    })
  },
  submit: function () {
    if (this._busy || this.data.phase === 'bound' || this.data.phase === 'rate' || this.data.phase === 'redeeming' || this.data.phase === 'loading' || this.data.phase === 'guest' || this.data.phase === 'unavailable') return
    var code = this.data.code || ''
    if (code.length < 8) return
    var self = this
    if (require('../../config').mode === 'live') {
      this._busy = true
      this.setData({ phase: 'redeeming', code: code, fieldError: false })
      Promise.resolve().then(function () { return api.redeem(code) }).then(function (result) {
        self._busy = false
        self.showResult(result, code)
      }, function () {
        self._busy = false
        self.setData({ phase: 'error', message: '网络异常，请重试', code: code, fieldError: false })
      })
      return
    }
    this.setData({ phase: 'redeeming', code: code, fieldError: false })
    nav.later(500, function () {
      self.showResult(api.redeem(code), code)
    })
  },
  profile: function () { wx.switchTab({ url: '/pages/profile/index' }) },
  home: function () { wx.switchTab({ url: '/pages/home/index' }) }
})
