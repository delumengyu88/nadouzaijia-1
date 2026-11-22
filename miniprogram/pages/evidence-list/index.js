var api = require('../../api/index')
var nav = require('../../utils/nav')

Page({
  data: { title: '取证申请', showBack: true, rows: [] },
  onShow: function () {
    var data = nav.layout('evidence-list', this.query)
    data.title = '取证申请'
    data.showBack = true
    data.rows = api.listEvidence()
    data.empty = !data.rows.length
    this.setData(data)
  },
  back: function () {
    if (getCurrentPages().length > 1) wx.navigateBack()
    else wx.switchTab({ url: '/pages/profile/index' })
  },
  open: function (e) {
    wx.navigateTo({ url: '/pages/evidence-detail/index?id=' + e.currentTarget.dataset.id })
  },
  create: function () {
    wx.navigateTo({ url: '/pages/evidence/index?kind=normal' })
  }
})
