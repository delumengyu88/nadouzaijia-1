var api = require('../../api/index')
var nav = require('../../utils/nav')

function decorate(rows) {
  return (rows || []).map(function (row, index) {
    return {
      id: row.id,
      name: row.name || '',
      badge: row.badge || '',
      badgeCls: row.badgeCls || '',
      line: row.line || '',
      key: (row.id || '') + ':' + index + ':' + (row.name || '')
    }
  })
}

Page({
  data: { title: '查询记录', showBack: true, rows: [], empty: false, loading: false, failed: false, needLogin: false },
  onShow: function () {
    var data = nav.layout('records', this.query)
    data.title = '查询记录'
    data.showBack = true
    var listed = api.listRecords()
    if (listed && typeof listed.then === 'function') {
      data.loading = true
      data.rows = []
      data.empty = false
      data.failed = false
      data.needLogin = false
      this.setData(data)
      var self = this
      var token = this.loadToken = (this.loadToken || 0) + 1
      listed.then(function (rows) {
        if (self.loadToken !== token) return
        var list = rows || []
        self.setData({ loading: false, failed: false, needLogin: false, rows: decorate(list), empty: !list.length })
      }, function (error) {
        if (self.loadToken !== token) return
        self.showError(error)
      })
      return
    }
    var rows = decorate(listed || [])
    data.rows = rows
    data.empty = !rows.length
    data.loading = false
    data.failed = false
    data.needLogin = false
    this.setData(data)
  },
  showError: function (error) {
    var code = error && error.statusCode
    if (code === 401 || (error && error.message === '请先登录')) {
      this.setData({ loading: false, failed: false, empty: false, rows: [], needLogin: true })
      return
    }
    var network = code === 0 || code >= 500 || (error && error.message === '网络异常，请重试')
    this.setData({ loading: false, failed: !!network, empty: false, rows: [], needLogin: false })
  },
  retryLoad: function () { this.onShow() },
  goQuery: function () { wx.switchTab({ url: '/pages/home/index' }) },
  login: function () { return require('../../utils/login')(this, this.onShow) },
  back: function () {
    if (getCurrentPages().length > 1) wx.navigateBack()
    else wx.switchTab({ url: '/pages/profile/index' })
  },
  open: function (e) {
    wx.navigateTo({ url: '/pages/query/index?id=' + e.currentTarget.dataset.id })
  }
})
