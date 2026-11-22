var config = require('../config')
var mode = config.mode || (config.useMock === false ? 'live' : 'mock')
var api = mode === 'live' ? require('./http') : require('./mock')
var client = Object.assign({}, api, mode === 'mock' ? {} : require('./live'))
;['checkout', 'startPay', 'repay', 'settle', 'setOrderStatus', 'exportBook'].forEach(function (method) {
  var original = client[method]
  client[method] = function () {
    if (config.calibrating) return require('../utils/calibration').blockedResult()
    return original.apply(this, arguments)
  }
})
module.exports = client
