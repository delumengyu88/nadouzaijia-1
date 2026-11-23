var config = require('../config')
var testOffer = require('./live-test-offer')
var diamond = require('./diamond')
var mode = config.mode || (config.useMock === false ? 'live' : 'mock')
var api = mode === 'live' ? require('./http') : require('./mock')
var client = Object.assign({}, api, mode === 'mock' ? {} : require('./live'))
;['checkout', 'startPay', 'repay', 'settle', 'setOrderStatus', 'exportBook', 'exportQuery'].forEach(function (method) {
  var original = client[method]
  client[method] = function () {
    if (method === 'checkout' && arguments[0] === diamond.id && !diamond.enabled()) return Promise.resolve(diamond.unavailable())
    if (method === 'checkout' && (arguments[0] === testOffer.id ? !testOffer.enabled() : testOffer.dedicated())) {
      return Promise.resolve(testOffer.unavailable())
    }
    if (testOffer.dedicated() && (method === 'startPay' || method === 'repay')) return Promise.resolve(testOffer.unavailable())
    if (config.calibrating) return require('../utils/calibration').blockedResult()
    return original.apply(this, arguments)
  }
})
client.login = require('./auth').login
client.ensureEntry = function () {
  var state = require('./state')
  if (state.token()) return Promise.resolve(state.load().auth.user)
  if (config.mode === 'mock') {
    if (api.accountKind() === 'guest') api.switchAccount('free')
    return Promise.resolve(api.profile())
  }
  return client.login()
}
module.exports = client
