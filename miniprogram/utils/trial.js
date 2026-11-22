var config = require('../config')
var MESSAGE = '体验版暂不支持支付，请找客服开通'
function blockPayment() {
  if (!config.trialMode) return false
  wx.showModal({ title: '提示', content: MESSAGE, showCancel: false })
  return true
}
function mockPage(page, query) {
  if (!config.trialMode || config.mode === 'live' || page === 'splash') return false
  if (config.mode === 'mock') return true
  if (page === 'query') return false
  if (page === 'report' || page === 'viewer') return !!(query && query.example === '1')
  return true
}
module.exports = { blockPayment: blockPayment, mockPage: mockPage, message: MESSAGE }
