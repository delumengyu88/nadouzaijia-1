var config = require('../config')
var mode = config.mode || (config.useMock === false ? 'live' : 'mock')
var api = mode === 'live' ? require('./http') : require('./mock')
module.exports = mode === 'mock' ? api : Object.assign({}, api, require('./live'))
