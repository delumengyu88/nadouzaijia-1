var config = require('../config')
var MESSAGE = '数据校准中，校准完成后可查看结果'
function blockedResult() {
  return { ok: false, code: 'calibrating', message: MESSAGE }
}
function blockReport(query) {
  if (!config.calibrating) return false
  wx.redirectTo({ url: '/pages/query/index?id=' + encodeURIComponent(query && query.id || '') })
  return true
}
module.exports = { blockedResult: blockedResult, blockReport: blockReport, message: MESSAGE }
