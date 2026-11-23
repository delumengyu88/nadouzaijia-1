// 单节目分档解锁：一次买断「本节目 + 一个时间段」，价格与可购状态只认服务端。
var request = require('./request')
var config = require('../config')

var IDS = ['unlock_band_90_180', 'unlock_band_30_90']
var LABELS = { unlock_band_90_180: '90–180天', unlock_band_30_90: '30–90天' }

function known(id) {
  return IDS.indexOf(id) !== -1
}

function allowed(id) {
  if (!known(id)) return false
  if (config.mode !== 'live' || config.calibrating || config.trialMode || config.liveOneYuanTest) return false
  return true
}

function priceText(fen) {
  if (!Number.isSafeInteger(fen) || fen <= 0) return ''
  var yuan = Math.floor(fen / 100)
  var cents = fen % 100
  if (!cents) return '¥' + yuan
  return '¥' + yuan + '.' + (cents % 10 ? String(cents < 10 ? '0' + cents : cents) : String(cents / 10))
}

function unavailable(message) {
  return { ok: false, message: message || '本节目这一时间段暂不可解锁' }
}

// Reads GET /api/v1/unlocks/offer. Older servers answer 404 and the page says so plainly.
function offer(name, id) {
  if (!known(id) || !name) return Promise.resolve(unavailable())
  var path = '/api/v1/unlocks/offer?product=' + encodeURIComponent(id) + '&program_name=' + encodeURIComponent(name)
  return request(path, {}, { exposeStatus: true }).then(function (res) {
    var body = res && res.data
    if (!res || res.statusCode < 200 || res.statusCode >= 300 || !body || typeof body !== 'object') {
      var detail = body && typeof body.detail === 'string' ? body.detail : ''
      if (res && res.statusCode === 404) detail = '当前版本暂不支持单节目分档解锁'
      return unavailable(detail)
    }
    if (body.id !== id || !Number.isSafeInteger(body.amount_fen) || body.amount_fen <= 0) return unavailable()
    var count = Number.isSafeInteger(body.count) && body.count > 0 ? body.count : 0
    if (!count) return unavailable('本节目这一时间段没有可解锁的记录')
    return {
      ok: true,
      id: id,
      programName: typeof body.program_name === 'string' && body.program_name ? body.program_name : name,
      band: LABELS[id],
      bandLabel: typeof body.band_label === 'string' && body.band_label ? body.band_label : LABELS[id],
      count: count,
      amountFen: body.amount_fen,
      price: priceText(body.amount_fen),
      purchasable: body.purchasable === true
    }
  }, function (error) {
    return unavailable(error && error.message === '网络异常，请重试' ? '网络异常，请重试' : '')
  })
}

module.exports = { IDS: IDS, LABELS: LABELS, known: known, allowed: allowed, offer: offer, priceText: priceText }
