var config = require('../config')
var MESSAGE = '体验版暂不支持支付，请找客服开通'
function blockPayment() {
  if (!config.trialMode) return false
  wx.showModal({ title: '提示', content: MESSAGE, showCancel: false })
  return true
}
module.exports = { blockPayment: blockPayment, message: MESSAGE }
