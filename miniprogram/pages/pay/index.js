var api = require('../../api/index')
var nav = require('../../utils/nav')

function decode(value) {
  if (!value) return ''
  try { return decodeURIComponent(value) } catch (err) { return value }
}

function isPromise(value) {
  return !!(value && typeof value.then === 'function')
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
  covered: '无需购买',
  unavailable: '支付暂未开通'
}

var CARDS = {
  membership_vip: 'VIP',
  membership_regular: '普通会员',
  membership_test: '支付测试'
}

Page({
  pollInterval: 2000,
  pollLimit: 10,
  data: {
    showBack: true,
    phase: 'pending',
    payLoading: false,
    mockSettle: false,
    confirmTimeout: false,
    confirmSpin: false,
    resultTitle: '',
    resultSub: '',
    centerTitle: '',
    centerSub: ''
  },
  onLoad: function (query) {
    this.product = query.product || 'membership_vip'
    this.programName = decode(query.name)
    this.orderNo = query.id || ''
    this.livePayFlow = require('../../config').mode === 'live'
    this._pollToken = 0
    var layout = nav.layout('pay', this.query)
    this.setData({ statusBarHeight: layout.statusBarHeight, navHeight: layout.navHeight, showBack: true })
    if (require('../../config').trialMode || require('../../config').calibrating) {
      this.setData({ phase: 'trial', title: '支付', trialMessage: require('../../utils/trial').paymentMessage() })
      return
    }
    if (query.view) {
      this.showPreset(query.view)
      return
    }
    this.open()
  },
  onUnload: function () {
    this._pollToken = (this._pollToken || 0) + 1
  },
  back: function () {
    if (getCurrentPages().length > 1) wx.navigateBack()
    else wx.switchTab({ url: '/pages/member/index' })
  },
  showPreset: function (view) {
    var map = {
      paying: { phase: 'paying', title: '支付中', mockSettle: true },
      granting: { phase: 'granting', title: '权益发放中' },
      success: { phase: 'granted', title: '开通成功', successLine: 'VIP 已发放，有效至 2026-12-22' },
      failure: { phase: 'failed', title: '支付失败', allowCheck: true, allowRepay: false },
      unpaid: { phase: 'failed', title: '支付失败', allowCheck: false, allowRepay: true },
      cancelled: { phase: 'cancelled', title: '已取消' },
      abnormal: { phase: 'grant_error', title: '发放异常' },
      covered: { phase: 'covered', title: '无需购买' },
      unavailable: { phase: 'unavailable', title: '支付暂未开通' },
      confirming: { phase: 'confirming', title: '支付确认中', confirmSpin: true, resultTitle: '正在确认支付结果…' },
      timeout: {
        phase: 'confirming',
        title: '支付结果确认中',
        confirmSpin: false,
        confirmTimeout: true,
        resultTitle: '正在确认支付结果…',
        resultSub: '暂未收到最终结果，请稍后刷新，请勿重复支付。'
      }
    }
    this.setData(map[view] || { phase: 'pending', title: '确认开通' })
  },
  open: function () {
    if (this.orderNo) {
      var existing = api.getOrder(this.orderNo)
      if (isPromise(existing)) {
        this.followGet(existing)
        return
      }
      if (existing) this.apply(existing)
      return
    }
    if (this.livePayFlow) {
      this.setData({
        phase: 'pending',
        title: '确认开通',
        card: CARDS[this.product] || '确认开通',
        payText: '立即支付',
        showPay: true,
        blurb: '',
        priceText: '',
        mockSettle: false
      })
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
  followGet: function (pending) {
    var self = this
    this.setData({ payLoading: true })
    pending.then(function (order) {
      self.setData({ payLoading: false })
      if (order) self.apply(order)
      else wx.showToast({ title: '订单不存在', icon: 'none' })
    }, function () {
      self.setData({ payLoading: false })
      wx.showToast({ title: '请稍后重试', icon: 'none' })
    })
  },
  apply: function (order) {
    var phase = order.status
    if (phase === 'failed' && order.allowRepay) phase = 'failed'
    this.setData({
      phase: phase,
      title: order.navTitle || TITLES[phase] || '确认开通',
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
      showPay: order.showPay,
      mockSettle: !this.livePayFlow && phase === 'paying',
      confirmTimeout: false,
      confirmSpin: false,
      centerTitle: order.centerTitle || '',
      centerSub: order.centerSub || ''
    })
  },
  pay: function () {
    if (require('../../utils/trial').blockPayment()) return
    if (this.livePayFlow) {
      if (this.data.payLoading) return
      if (this.data.phase === 'paying' || this.data.phase === 'confirming' || this.data.phase === 'granting' || this.data.phase === 'granted') return
      this.setData({ payLoading: true })
      var pending = this.orderNo ? api.startPay(this.orderNo) : api.checkout(this.product, this.programName)
      this.afterOrder(pending)
      return
    }
    var started = api.startPay(this.orderNo)
    if (!started.ok) {
      wx.showToast({ title: started.message, icon: 'none' })
      if (started.order) this.apply(started.order)
      return
    }
    this.apply(started.order)
  },
  afterOrder: function (pending) {
    var self = this
    Promise.resolve(pending).then(function (result) {
      if (!result || result.code === 'unconfigured') {
        self.setData({
          payLoading: false,
          phase: 'unavailable',
          title: '支付暂未开通',
          showPay: false,
          mockSettle: false,
          confirmTimeout: false
        })
        return
      }
      if (result.code === 'covered') {
        self.setData({ payLoading: false, phase: 'covered', title: '无需购买', showPay: false })
        return
      }
      if (!result.ok) {
        self.setData({ payLoading: false })
        wx.showToast({ title: result.message || '请稍后重试', icon: 'none' })
        if (result.order && result.order.status) self.apply(result.order)
        return
      }
      self.orderNo = result.order.orderNo
      if (result.pay) {
        self.setData({
          payLoading: false,
          phase: 'paying',
          title: '支付中',
          orderNo: result.order.orderNo,
          showPay: false,
          mockSettle: false,
          card: result.order.card,
          priceText: result.order.priceText,
          blurb: result.order.blurb,
          payText: result.order.payText
        })
        self.invokePayment(result.pay)
        return
      }
      self.setData({ payLoading: false })
      self.apply(result.order)
    }, function () {
      self.setData({ payLoading: false })
      wx.showToast({ title: '请稍后重试', icon: 'none' })
    })
  },
  invokePayment: function (pay) {
    var self = this
    var once = false
    function done() {
      if (once) return
      once = true
      self.beginPoll()
    }
    try {
      wx.requestPayment({
        timeStamp: pay.timeStamp,
        nonceStr: pay.nonceStr,
        package: pay.package,
        signType: pay.signType,
        paySign: pay.paySign,
        success: function () { done() },
        fail: function () { done() }
      })
    } catch (err) {
      done()
    }
  },
  beginPoll: function () {
    this._pollToken = (this._pollToken || 0) + 1
    var token = this._pollToken
    this.setData({
      phase: 'confirming',
      title: '支付确认中',
      confirmTimeout: false,
      confirmSpin: true,
      resultTitle: '正在确认支付结果…',
      resultSub: '',
      payLoading: false,
      mockSettle: false,
      showPay: false
    })
    this.poll(token, 0)
  },
  poll: function (token, count) {
    var self = this
    if (token !== this._pollToken) return
    Promise.resolve(api.checkStatus(this.orderNo)).then(function (order) {
      if (token !== self._pollToken) return
      var code = order && (order.code || order.status)
      var phase = order && order.status
      if (code === 'unconfigured' || code === 'unavailable' || phase === 'unavailable') {
        self.setData({ phase: 'unavailable', title: '支付暂未开通', payLoading: false, confirmTimeout: false, confirmSpin: false, showPay: false })
        return
      }
      if (phase === 'granted' || phase === 'grant_error' || phase === 'cancelled' || phase === 'covered' || phase === 'failed') {
        self.apply(order)
        return
      }
      if (phase === 'granting') self.apply(order)
      var next = count + 1
      if (next >= self.pollLimit) {
        if (self.data.phase === 'granting') {
          self.setData({ confirmTimeout: true, payLoading: false })
          return
        }
        self.setData({
          phase: 'confirming',
          title: '支付结果确认中',
          confirmTimeout: true,
          confirmSpin: false,
          resultTitle: '正在确认支付结果…',
          resultSub: '暂未收到最终结果，请稍后刷新，请勿重复支付。',
          payLoading: false
        })
        return
      }
      setTimeout(function () {
        if (token !== self._pollToken) return
        self.poll(token, next)
      }, self.pollInterval)
    }, function () {
      if (token !== self._pollToken) return
      self.setData({ payLoading: false })
      wx.showToast({ title: '请稍后重试', icon: 'none' })
    })
  },
  showChecked: function (order) {
    if (!order) return
    var code = order.code || order.status
    if (code === 'unconfigured' || code === 'unavailable') {
      this.setData({ phase: 'unavailable', title: '支付暂未开通', payLoading: false, showPay: false, confirmTimeout: false })
      return
    }
    if (code === 'pending') {
      this.apply(Object.assign({}, order, {
        status: 'failed',
        code: 'unpaid',
        allowRepay: true,
        allowCheck: false,
        showPay: false
      }))
      return
    }
    this.apply(order)
  },
  settle: function (e) {
    if (require('../../utils/trial').blockPayment()) return
    if (this.livePayFlow) {
      var self = this
      var outcome = e && e.currentTarget && e.currentTarget.dataset ? e.currentTarget.dataset.outcome : ''
      Promise.resolve(api.settle(this.orderNo, outcome)).then(function (result) {
        var order = result && result.order
        if (order) self.showChecked(order)
      })
      return
    }
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
    if (this.data.payLoading) return
    var order = api.checkStatus(this.orderNo)
    if (isPromise(order)) {
      this.setData({ payLoading: true })
      var self = this
      order.then(function (view) {
        self.setData({ payLoading: false })
        if (view) self.showChecked(view)
      }, function () {
        self.setData({ payLoading: false })
        wx.showToast({ title: '请稍后重试', icon: 'none' })
      })
      return
    }
    if (order) this.apply(order)
  },
  refreshStatus: function () {
    if (this.data.confirmSpin || this.data.payLoading) return
    this.setData({ confirmSpin: true, confirmTimeout: false, payLoading: true, resultSub: '' })
    this.beginPoll()
  },
  repay: function () {
    if (require('../../utils/trial').blockPayment()) return
    if (this.data.payLoading) return
    var result = api.repay(this.orderNo)
    if (isPromise(result)) {
      this.setData({ payLoading: true })
      this.afterOrder(result)
      return
    }
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
