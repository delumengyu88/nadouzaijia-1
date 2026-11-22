var api = require('../../api/index')
var nav = require('../../utils/nav')

Page({
  data: { title: '取证申请', showBack: true, rows: [], loading: true, empty: false, loadError: '' },
  onShow: function () {
    var layout = nav.layout('evidence-list', this.query)
    var listed = api.listEvidence()
    var self = this
    function apply(rows, failed) {
      var list = rows || []
      self.setData({
        statusBarHeight: layout.statusBarHeight,
        navHeight: layout.navHeight,
        title: '取证申请',
        showBack: true,
        loading: false,
        loadError: failed ? '网络异常，请重试' : '',
        rows: failed ? [] : list,
        empty: !failed && !list.length
      })
    }
    if (listed && typeof listed.then === 'function') {
      this.setData({
        statusBarHeight: layout.statusBarHeight,
        navHeight: layout.navHeight,
        title: '取证申请',
        showBack: true,
        loading: true,
        rows: [],
        empty: false,
        loadError: ''
      })
      listed.then(function (rows) { apply(rows, false) }, function () { apply([], true) })
      return
    }
    apply(listed, false)
  },
  back: function () {
    if (getCurrentPages().length > 1) wx.navigateBack()
    else wx.switchTab({ url: '/pages/profile/index' })
  },
  open: function (e) {
    wx.navigateTo({ url: '/pages/evidence-detail/index?id=' + encodeURIComponent(e.currentTarget.dataset.id) })
  },
  create: function () {
    wx.navigateTo({ url: '/pages/evidence/index?kind=normal' })
  },
  retry: function () { this.onShow() }
})
