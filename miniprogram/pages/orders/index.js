var api = require('../../api/index')
var nav = require('../../utils/nav')

var STATUS_LABEL = {
  pending: '待支付',
  paid: '已支付',
  granted: '已发放',
  grant_error: '发放异常',
  refunded: '已退款'
}

function textOf(value) {
  if (value == null) return ''
  return String(value).trim()
}

function presentLine(line, statusText) {
  var text = line == null ? '' : String(line)
  if (!statusText || statusText === '—') return text
  var prefix = statusText + ' · '
  if (text.indexOf(prefix) === 0) return text.slice(prefix.length)
  return text
}

function presentOrder(item) {
  var source = item || {}
  var title = textOf(source.title) || '会员订单'
  var statusText = textOf(source.statusText) || STATUS_LABEL[source.status] || '—'
  var kind = ''
  if (source.status === 'granted') kind = 'granted'
  else if (source.status === 'pending') kind = 'pending'
  else if (source.status === 'grant_error') kind = 'error'
  else if (source.status === 'refunded') kind = 'refunded'
  return {
    orderNo: source.orderNo,
    title: title,
    line: presentLine(source.line, statusText),
    status: source.status,
    statusText: statusText,
    isTest: source.isTest === true,
    statusKind: kind,
    service: source.status === 'grant_error'
  }
}

Page({
  data: {
    title: '我的订单',
    showBack: true,
    rows: [],
    empty: false,
    loading: false,
    failed: false,
    needLogin: false,
    live: false
  },
  onShow: function () {
    var data = nav.layout('orders', this.query)
    data.title = '我的订单'
    data.showBack = true
    if (require('../../config').mode === 'live') {
      data.live = true
      data.failed = false
      data.empty = false
      data.rows = []
      if (!require('../../api/state').token()) {
        data.loading = false
        data.needLogin = true
        this.setData(data)
        return
      }
      data.loading = true
      data.needLogin = false
      this.setData(data)
      this.fetchOrders()
      return
    }
    var rows = api.listOrders()
    data.live = false
    data.loading = false
    data.failed = false
    data.needLogin = false
    data.rows = rows
    data.empty = !rows.length
    this.setData(data)
  },
  fetchOrders: function () {
    var self = this
    var ticket = (this.ticket || 0) + 1
    this.ticket = ticket
    var pending
    try {
      pending = api.listOrders()
    } catch (error) {
      this.failLoad(ticket)
      return
    }
    if (!pending || typeof pending.then !== 'function') {
      this.failLoad(ticket)
      return
    }
    pending.then(function (body) {
      if (ticket !== self.ticket) return
      if (!Array.isArray(body)) {
        self.failLoad(ticket)
        return
      }
      self.setData({
        loading: false,
        failed: false,
        needLogin: false,
        live: true,
        rows: body.map(presentOrder),
        empty: body.length === 0
      })
    }, function (error) {
      if (ticket !== self.ticket) return
      if (error && error.message === '请先登录') {
        self.setData({ loading: false, failed: false, needLogin: true, empty: false, rows: [], live: true })
        return
      }
      self.failLoad(ticket)
    })
  },
  failLoad: function (ticket) {
    if (ticket != null && ticket !== this.ticket) return
    this.setData({ loading: false, failed: true, needLogin: false, empty: false, rows: [], live: true })
  },
  retry: function () {
    this.onShow()
  },
  login: function () {
    return require('../../utils/login')(this, this.onShow)
  },
  member: function () {
    wx.switchTab({ url: '/pages/member/index' })
  },
  back: function () {
    if (getCurrentPages().length > 1) wx.navigateBack()
    else wx.switchTab({ url: '/pages/profile/index' })
  },
  open: function (e) {
    if (this.data.live) return
    wx.navigateTo({ url: '/pages/pay/index?id=' + e.currentTarget.dataset.id })
  },
  noop: function () {}
})
