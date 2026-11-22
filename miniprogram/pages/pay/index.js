var api = require('../../api/index')
var nav = require('../../utils/nav')

function decode(value) {
  if (!value) return ''
  try { return decodeURIComponent(value) } catch (err) { return value }
}

var TITLES = {
  pending: '确认开通',
  paying: '支付中',
  confirming: '支付确认中',
  granting: '权益发放中',
  granted: '开通成功',
  failed: '支付失败',
  cancelled: '已取消',
  grant_error: '发放异常',
  covered: '无需购买'
}

Page({
  data: { showBack: true, phase: 'pending' },
  onLoad: function (query) {
    this.product = query.product || 'membership_vip'
    this.programName = decode(query.name)
    this.orderNo = query.id || ''
    var layout = nav.layout('pay', this.query)
    this.setData({ statusBarHeight: layout.statusBarHeight, navHeight: layout.navHeight, showBack: true })
    if (require('../../config').trialMode) {
      this.setData({ phase: 'trial', title: '支付', trialMessage: require('../../utils/trial').message })
      return
    }
    if (query.view) {
      this.showPreset(query.view)
      return
    }
    this.open()
  },
  back: function () {
    if (getCurrentPages().length > 1) wx.navigateBack()
    else wx.switchTab({ url: '/pages/member/index' })
  },
  showPreset: function (view) {
    var map = {
      paying: { phase: 'paying', title: '支付中' },
      confirming: { phase: 'confirming', title: '支付确认中' },
      granting: { phase: 'granting', title: '权益发放中' },
      success: { phase: 'granted', title: '开通成功', successLine: 'VIP 已发放，有效至 2026-12-22' },
      failure: { phase: 'failed', title: '支付失败', allowCheck: true, allowRepay: false, orderNo: 'DD20261119005' },
      unpaid: { phase: 'failed', title: '支付失败', allowCheck: false, allowRepay: true, orderNo: 'DD20261119005' },
      cancelled: { phase: 'cancelled', title: '已取消' },
      abnormal: { phase: 'grant_error', title: '发放异常', orderNo: 'DD20261122018' },
      covered: { phase: 'covered', title: '无需购买' }
    }
    this.setData(map[view] || { phase: 'pending', title: '确认开通' })
  },
  open: function () {
    if (this.orderNo) {
      var existing = api.getOrder(this.orderNo)
      if (existing) this.apply(existing)
      return
    }
    var result = api.checkout(this.product, this.programName)
    if (!result.ok && result.code === 'covered') {
      this.setData({ phase: 'covered', title: '无需购买' })
      return
    }
    if (!result.ok) {
      wx.showToast({ title: result.message || '请先登录', icon: 'none' })
      return
    }
    this.orderNo = result.order.orderNo
    this.apply(result.order)
  },
  apply: function (order) {
    var phase = order.status
    if (phase === 'failed' && order.allowRepay) phase = 'failed'
    this.setData({
      phase: phase,
      title: TITLES[phase] || '确认开通',
      orderNo: order.orderNo,
      card: order.card,
      priceText: order.priceText,
      blurb: order.blurb,
      renewLine: order.renewLine,
      coverLine: order.coverLine,
      payText: order.payText,
      allowCheck: order.allowCheck,
      allowRepay: order.allowRepay,
      successLine: order.successLine,
      showPay: order.showPay
    })
  },
  pay: function () {
    if (require('../../utils/trial').blockPayment()) return
    var started = api.startPay(this.orderNo)
    if (!started.ok) {
      wx.showToast({ title: started.message, icon: 'none' })
      if (started.order) this.apply(started.order)
      return
    }
    this.apply(started.order)
  },
  settle: function (e) {
    if (require('../../utils/trial').blockPayment()) return
    var outcome = e.currentTarget.dataset.outcome
    var self = this
    if (outcome === 'success') {
      api.setOrderStatus(this.orderNo, 'confirming')
      this.setData({ phase: 'confirming', title: '支付确认中' })
      nav.later(500, function () {
        api.setOrderStatus(self.orderNo, 'granting')
        self.setData({ phase: 'granting', title: '权益发放中' })
        nav.later(500, function () {
          var done = api.settle(self.orderNo, 'success')
          if (done.order) self.apply(done.order)
        })
      })
      return
    }
    var done = api.settle(this.orderNo, outcome)
    if (done.order) this.apply(done.order)
  },
  check: function () {
    var order = api.checkStatus(this.orderNo)
    if (order) this.apply(order)
  },
  repay: function () {
    if (require('../../utils/trial').blockPayment()) return
    var result = api.repay(this.orderNo)
    if (!result.ok) {
      wx.showToast({ title: result.message, icon: 'none' })
      return
    }
    this.apply(result.order)
  },
  finish: function () {
    if (this.programName) {
      wx.navigateBack()
      return
    }
    wx.switchTab({ url: '/pages/member/index' })
  }
})
