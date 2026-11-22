var config = require('../config')
var testOffer = require('./live-test-offer')
var mode = config.mode || (config.useMock === false ? 'live' : 'mock')
var api = mode === 'live' ? require('./http') : require('./mock')
var client = Object.assign({}, api, mode === 'mock' ? {} : require('./live'))
;['checkout', 'startPay', 'repay', 'settle', 'setOrderStatus', 'exportBook'].forEach(function (method) {
  var original = client[method]
  client[method] = function () {
    if (method === 'checkout' && (arguments[0] === testOffer.id ? !testOffer.enabled() : testOffer.dedicated())) {
      return Promise.resolve(testOffer.unavailable())
    }
    if (testOffer.dedicated() && (method === 'startPay' || method === 'repay')) return Promise.resolve(testOffer.unavailable())
    if (config.calibrating) return require('../utils/calibration').blockedResult()
    return original.apply(this, arguments)
  }
})
client.login = require('./auth').login
module.exports = client
