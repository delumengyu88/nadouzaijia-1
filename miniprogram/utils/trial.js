var config = require('../config')
var MESSAGE = '体验版暂不支持支付，请找客服开通'
function paymentMessage() {
  return config.trialMode ? MESSAGE : require('./calibration').message
}
function blockPayment() {
  if (!config.trialMode && !config.calibrating) return false
  wx.showModal({ title: '提示', content: paymentMessage(), showCancel: false })
  return true
}
module.exports = { blockPayment: blockPayment, message: MESSAGE, paymentMessage: paymentMessage }
