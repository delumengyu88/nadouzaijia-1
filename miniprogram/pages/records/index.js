var api = require('../../api/index')
var nav = require('../../utils/nav')

var state = require('../../api/state')
var PAGE_SIZE = 20

function sessionKey() {
  return api.isLive ? (state.queryToken() || state.pendingGuestToken()) : state.currentId()
}

function decorate(rows, offset) {
  return (rows || []).map(function (row, index) {
    return {
      id: row.id,
      name: row.name || '',
      badge: row.badge || '',
      badgeCls: row.badgeCls || '',
      line: row.line || '',
      key: (row.id || '') + ':' + (offset + index) + ':' + (row.name || '')
    }
  })
}

Page({
  data: { title: '查询记录', showBack: true, rows: [], empty: false, loading: false, failed: false, needLogin: false },
  onIdentityReady: function () { this.onShow() },
  onShow: function () {
    require('../../utils/login').onShow(this)
    if (this.returning && this.session === sessionKey()) {
      this.returning = false
      return
    }
    this.returning = false
    this.session = sessionKey()
    this.loadToken = (this.loadToken || 0) + 1
    this.setData(Object.assign(nav.layout('records', this.query), {
      title: '查询记录', showBack: true, rows: [], loading: true, empty: false,
      failed: false, needLogin: false, hasMore: false, loadingMore: false, moreError: false
    }))
    this.fetchPage(false)
  },
  onUnload: function () { this.loadToken += 1 },
  loadMore: function () {
    if (this.session !== sessionKey()) { this.onShow(); return }
    if (!this.data.hasMore || this.data.loadingMore) return
    this.setData({ loadingMore: true, moreError: false })
    this.fetchPage(true)
  },
  fetchPage: function (append) {
    var self = this
    var token = this.loadToken
    var count = this.data.rows.length
    // Overlap one row to detect inserts/deletes that shift the server offset between pages.
    var offset = append ? count - 1 : 0
    var result = api.listRecordPage(append ? PAGE_SIZE + 1 : PAGE_SIZE, offset)
    function apply(page) {
      if (token !== self.loadToken) return
      if (self.session !== sessionKey()) { self.onShow(); return }
      var rows = decorate(page.rows, offset)
      if (append) {
        var anchor = self.data.rows[count - 1]
        if (!rows[0] || rows[0].key !== anchor.key) { self.onShow(); return }
        rows = self.data.rows.concat(rows.slice(1))
      }
      self.setData({ rows: rows, empty: !rows.length, loading: false, loadingMore: false, hasMore: page.hasMore })
    }
    function failed(error) {
      if (token !== self.loadToken) return
      if ((error && error.sessionChanged) || self.session !== sessionKey()) { self.onShow(); return }
      if (append && error && error.statusCode !== 401 && error.statusCode !== 403) {
        self.setData({ loadingMore: false, moreError: true })
      } else {
        self.setData({ loadingMore: false, hasMore: false, moreError: false })
        self.showError(error)
      }
    }
    if (result && typeof result.then === 'function') result.then(apply, failed)
    else apply(result)
  },
  showError: function (error) {
    var code = error && error.statusCode
    if (code === 401 || (error && error.message === '请先登录')) {
      this.setData({ loading: false, failed: false, empty: false, rows: [], needLogin: true, guestExpired: !!state.load().guestExpired })
      return
    }
    var network = code === 0 || code >= 500 || (error && error.message === '网络异常，请重试')
    var failureText = network ? '网络异常，请重试' : (code === 403 ? '当前账户无权查看查询记录' : '查询记录暂不可用')
    this.setData({ loading: false, failed: true, failureText: failureText, empty: false, rows: [], needLogin: false })
  },
  retryLoad: function () { this.onShow() },
  goQuery: function () { wx.switchTab({ url: '/pages/home/index' }) },
  back: function () {
    if (getCurrentPages().length > 1) wx.navigateBack()
    else wx.switchTab({ url: '/pages/profile/index' })
  },
  open: function (e) {
    if (this.session !== sessionKey()) { this.onShow(); return }
    this.returning = true
    wx.navigateTo({ url: '/pages/query/index?id=' + encodeURIComponent(e.currentTarget.dataset.id) })
  },
  requestEvidence: function (e) {
    if (this.session !== sessionKey()) { this.onShow(); return }
    var row = this.data.rows[e.currentTarget.dataset.index]
    if (!row) return
    this.returning = true
    wx.navigateTo({ url: '/pages/evidence/index?fromRecord=1&kind=normal&name=' + encodeURIComponent(row.name) })
  }
})
