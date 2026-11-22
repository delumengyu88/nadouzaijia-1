var api = require('../../api/index')
var nav = require('../../utils/nav')

var EMPTY = {
  missing: true,
  name: '—',
  badge: '—',
  badgeCls: 'muted',
  sub: '申请编号 —',
  phoneText: '—',
  quoteText: '—',
  reasonText: '—',
  refundText: '—',
  platformText: '—',
  noteText: '—',
  reason: '',
  hasRefund: false,
  canWithdraw: false,
  canConfirm: false,
  showPayBanner: false,
  steps: [],
  times: [],
  message: ''
}

Page({
  data: { title: '取证进度', showBack: true, loading: true, acting: false },
  onLoad: function (query) {
    this.id = query.id || ''
    this.query = query || {}
    var layout = nav.layout('evidence-detail', query)
    this.setData({
      statusBarHeight: layout.statusBarHeight,
      navHeight: layout.navHeight,
      showBack: true,
      title: '取证进度',
      loading: true
    })
    this.load()
  },
  load: function () {
    var detail = api.evidenceDetail(this.id)
    var self = this
    if (detail && typeof detail.then === 'function') {
      this.setData({ loading: true })
      return detail.then(function (view) { self.showDetail(view) }, function () { self.showDetail(null) })
    }
    this.showDetail(detail)
  },
  showDetail: function (detail) {
    var layout = nav.layout('evidence-detail', this.query)
    var base = {
      statusBarHeight: layout.statusBarHeight,
      navHeight: layout.navHeight,
      showBack: true,
      title: '取证进度',
      loading: false,
      acting: false
    }
    if (!detail) {
      this.setData(Object.assign({}, base, EMPTY))
      return
    }
    this.setData(Object.assign({}, base, detail, { missing: false }))
  },
  back: function () {
    if (getCurrentPages().length > 1) wx.navigateBack()
    else wx.switchTab({ url: '/pages/profile/index' })
  },
  ask: function (content, run) {
    wx.showModal({
      title: '请确认',
      content: content,
      confirmText: '确定',
      cancelText: '取消',
      success: function (res) {
        if (res && res.confirm) run()
      }
    })
  },
  withdraw: function () {
    var self = this
    this.ask('确定撤回这份取证申请？', function () { self.runAction('withdrawEvidence') })
  },
  confirm: function () {
    var self = this
    this.ask('确定已经收到取证材料？', function () { self.runAction('confirmEvidence') })
  },
  runAction: function (method) {
    if (this.data.acting) return
    this.setData({ acting: true })
    var result = api[method](this.id)
    var self = this
    function done(res) {
      self.setData({ acting: false })
      if (res && res.code === 'invalid_state') {
        wx.showToast({ title: '状态已变化', icon: 'none' })
        self.load()
        return
      }
      if (res && res.ok) {
        if (res.detail) self.showDetail(res.detail)
        else self.load()
        return
      }
      wx.showToast({ title: (res && res.message) || '网络异常，请重试', icon: 'none' })
    }
    if (result && typeof result.then === 'function') {
      result.then(done, function () { done({ ok: false, code: 'network', message: '网络异常，请重试' }) })
      return
    }
    done(result)
  }
})
