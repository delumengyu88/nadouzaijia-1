var api = require('../../api/index')
var nav = require('../../utils/nav')

Page({
  data: { title: '查询记录', showBack: true, rows: [] },
  onShow: function () {
    var data = nav.layout('records', this.query)
    data.title = '查询记录'
    data.showBack = true
    data.rows = api.listRecords()
    data.empty = !data.rows.length
    this.setData(data)
  },
  back: function () {
    if (getCurrentPages().length > 1) wx.navigateBack()
    else wx.switchTab({ url: '/pages/profile/index' })
  },
  open: function (e) {
    wx.navigateTo({ url: '/pages/query/index?id=' + e.currentTarget.dataset.id })
  }
})
