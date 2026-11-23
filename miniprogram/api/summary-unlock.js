var request = require('./request')
var config = require('../config')
var member = require('../utils/member')
var diamond = require('./diamond')

var labels = {
  membership_regular: '普通会员',
  membership_vip: 'VIP 会员',
  single_program_90d: '本节目单次解锁（90 天以上）',
  single_program_30d: '本节目单次解锁（30 天以上）'
}

function counts(input, hidden) {
  var result = {}
  Object.keys(labels).forEach(function (id) {
    var n = input && input[id]
    if (Number.isSafeInteger(n) && n > 0 && n <= hidden) result[id] = n
  })
  return result
}

function allowed(id) {
  if (config.mode !== 'live' || config.calibrating || config.liveOneYuanTest) return false
  if (id === diamond.id) return diamond.enabled()
  return !config.trialMode && (id === 'membership_regular' || id === 'membership_vip')
}

function offer(tiers, id) {
  var matching = (tiers || []).filter(function (r) { return r && r.id === id })
  if (matching.length !== 1 || !allowed(id)) return null
  var row = matching[0]
  if (row.purchasable !== true) return null
  if (id === diamond.id && !diamond.contextProduct(row)) return null
  var price = member.pricedTier(matching, id)
  return price && { id: id, title: id === diamond.id ? '体验会员' : labels[id],
    price: id === diamond.id ? '¥1/5 天' : member.priceText(price), purchasable: true }
}

function options(eligible, tiers) {
  var plans = []
  Object.keys(labels).forEach(function (id) {
    if (!eligible || !eligible[id]) return
    var plan = offer(tiers, id) || { id: id, title: labels[id], price: '', purchasable: false,
      pendingPrice: id.indexOf('single_program_') === 0 }
    plans.push(Object.assign(plan, { count: eligible[id] }))
    if (id === 'membership_vip') {
      var upgrade = offer(tiers, diamond.id)
      if (upgrade) plans.push(Object.assign(upgrade, { count: eligible[id] }))
    }
  })
  return plans
}

function catalog(name) {
  function read(virtual) {
    var query = 'program_name=' + encodeURIComponent(name) + (virtual ? '&channel=virtual' : '')
    return request('/api/v1/pay/tiers?' + query).then(function (body) {
      if (!body || !Array.isArray(body.tiers)) throw new Error('暂时无法读取解锁方案')
      return body.tiers
    })
  }
  if (config.virtualPayment || config.trialMode && diamond.enabled()) return read(true)
  if (!diamond.enabled()) return read(false)
  return Promise.all([read(false), read(true)]).then(function (all) {
    return all[0].filter(function (row) { return row.id !== diamond.id })
      .concat(all[1].filter(function (row) { return row.id === diamond.id }))
  })
}

module.exports = { counts: counts, allowed: allowed, offer: offer, options: options, catalog: catalog }
