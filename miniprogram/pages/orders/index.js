var api = require('../../api/index')
var nav = require('../../utils/nav')

Page({
  data: { title: '我的订单', showBack: true, rows: [] },
  onShow: function () {
    var data = nav.layout('orders', this.query)
    data.title = '我的订单'
    data.showBack = true
    data.rows = api.listOrders()
    data.empty = !data.rows.length
    this.setData(data)
  },
  back: function () {
    if (getCurrentPages().length > 1) wx.navigateBack()
    else wx.switchTab({ url: '/pages/profile/index' })
  },
  open: function (e) {
    wx.navigateTo({ url: '/pages/pay/index?id=' + e.currentTarget.dataset.id })
  }
})
