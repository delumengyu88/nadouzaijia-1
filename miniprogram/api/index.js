var config = require('../config')

module.exports = config.useMock ? require('./mock') : require('./http')
