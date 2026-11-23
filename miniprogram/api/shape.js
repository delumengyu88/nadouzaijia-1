var DATE = /^\d{4}-\d{2}-\d{2}$/

function emptySummary() {
  return {
    locked_total: 0,
    by_required_tier: { member: 0, vip: 0, evidence_only: 0 },
    unlockable_total: 0,
    cheapest_full_unlock: null,
    unlocks_by_product: {}
  }
}

function copyText(rec, key) {
  if (rec[key] === undefined || rec[key] === null || rec[key] === '') return undefined
  return String(rec[key])
}

// Accept only the authorized image fields of an unlocked response; never interpret storage locators.
function imageUrl(value) {
  if (typeof value !== 'string' || !/^https:\/\/[a-z0-9.-]+(?::[0-9]+)?\/[^\s\\#]+$/i.test(value)) return ''
  return value
}
function safeUrl(value) {
  if (require('../config').mode === 'mock' && typeof value === 'string' && value.indexOf('signed://derived/') === 0) return value
  return imageUrl(value)
}

function count(value) {
  return Number.isSafeInteger(value) && value >= 0 ? value : 0
}

// Visible summaries are already in records. Restricted summaries contribute only a count.
function summaryHitLine(search) {
  if (!search.summary_only_count) return ''
  var normal = (search.records || []).filter(function (rec) { return !rec.summary_only }).length
  var line = '命中 ' + (normal + search.summary_only_count) + ' 条'
  return search.platform_count > 0 ? line + ' · 涉及 ' + search.platform_count + ' 个平台' : line
}

var BAND_KEYS = ['ge_180', 'd90_179', 'd30_89', 'lt_30']
var BAND_STATES = ['open', 'locked', 'evidence']
var UNLOCK_PRODUCTS = ['unlock_band_90_180', 'unlock_band_30_90']

function frameView(value) {
  return value === 'clear' || value === 'blur' ? value : ''
}

// Result-page bands come only from servers that know them. Anything malformed drops
// the whole list so the page keeps its existing flat layout.
function sanitizeBands(value) {
  if (!Array.isArray(value) || !value.length) return null
  var seen = {}
  var bands = []
  for (var i = 0; i < value.length; i += 1) {
    var src = value[i] || {}
    if (BAND_KEYS.indexOf(src.band) === -1 || seen[src.band] || BAND_STATES.indexOf(src.state) === -1) return null
    seen[src.band] = true
    var band = {
      band: src.band,
      label: typeof src.label === 'string' && src.label ? src.label : '',
      count: count(src.count),
      state: src.state,
      frame_view: frameView(src.frame_view),
      unlocked_by: typeof src.unlocked_by === 'string' ? src.unlocked_by : '',
      unlock: null,
      upgrade_hint: typeof src.upgrade_hint === 'string' ? src.upgrade_hint : ''
    }
    var offer = src.unlock
    if (offer && UNLOCK_PRODUCTS.indexOf(offer.product) !== -1 && Number.isSafeInteger(offer.amount_fen) && offer.amount_fen > 0) {
      band.unlock = {
        product: offer.product,
        amount_fen: offer.amount_fen,
        price_text: typeof offer.price_text === 'string' && /^¥[0-9]+(\.[0-9]{1,2})?$/.test(offer.price_text) ? offer.price_text : '',
        purchasable: offer.purchasable === true
      }
    }
    bands.push(band)
  }
  bands.sort(function (a, b) { return BAND_KEYS.indexOf(a.band) - BAND_KEYS.indexOf(b.band) })
  return bands
}

function sanitizeSearch(body) {
  var src = body || {}
  var unresolved = src.outcome === 'unverified' || (src.outcome === 'miss' && src.unverified_source_count > 0)
  if (unresolved) {
    return {
      program_name: String(src.program_name || ''), outcome: 'unverified',
      platform_count: 0, unverified_source_count: Number(src.unverified_source_count) || 0,
      records: [], evidence_platforms: [], evidence_program_name: '',
      locked_summary: emptySummary(), locked_window: { count: 0, start: '', end: '' }, example: false
    }
  }
  var records = []
  var list = src.records || []
  if (!src.records) {
    ;(src.platform_groups || []).forEach(function (group) {
      list = list.concat(group.records || [])
    })
    list = list.concat(src.locked_records || [])
  }
  list.forEach(function (rec) {
    if (!rec) return
    if (rec.locked === true || rec.locked === 'true') {
      records.push({
        record_id: String(rec.record_id || ''),
        age_bucket: String(rec.age_bucket || 'unknown'),
        locked: true,
        lock_reason: String(rec.lock_reason || '')
      })
      return
    }
    if (rec.summary_only === true || rec.access === 'summary') {
      var item = { record_id: String(rec.record_id || ''), locked: false, access: 'summary', summary_only: true,
        can_download_report: false, can_download_screenshot: false }
      if (typeof rec.platform === 'string' && rec.platform) item.platform = rec.platform
      if (typeof rec.network_type === 'string' && rec.network_type) item.network_type = rec.network_type
      if (typeof rec.checked_on === 'string' && DATE.test(rec.checked_on)) item.checked_on = rec.checked_on
      if (Number.isSafeInteger(rec.frame_count) && rec.frame_count >= 0) item.frame_count = rec.frame_count
      records.push(item)
      return
    }
    var open = {
      record_id: String(rec.record_id || ''),
      age_bucket: String(rec.age_bucket || 'unknown'),
      locked: false,
      access: rec.access === 'full' ? 'full' : 'lowres',
      can_download_report: rec.can_download_report === true,
      can_download_screenshot: rec.access === 'full' && rec.can_download_screenshot === true
    }
    var category = copyText(rec, 'category')
    var subtype = copyText(rec, 'platform_subtype')
    if (category) open.category = category
    if (subtype) open.platform_subtype = subtype
    var platform = copyText(rec, 'platform')
    var network = copyText(rec, 'network_type')
    var checked = copyText(rec, 'checked_at')
    var thumb = safeUrl(rec.thumbnail_url)
    var hd = open.access === 'full' ? safeUrl(rec.hd_url) : ''
    if (platform) open.platform = platform
    if (network) open.network_type = network
    if (typeof rec.frame_count === 'number') open.frame_count = rec.frame_count
    if (checked) open.checked_at = checked
    if (Array.isArray(rec.timestamps)) open.timestamps = rec.timestamps.map(String)
    if (thumb) open.thumbnail_url = thumb
    if (hd) open.hd_url = hd
    if (frameView(rec.frame_view)) open.frame_view = rec.frame_view
    records.push(open)
  })
  var summary = src.locked_summary || emptySummary()
  var tier = summary.by_required_tier || {}
  var lockedCount = 0
  records.forEach(function (rec) {
    if (rec.locked && rec.age_bucket !== 'lt_30') lockedCount += 1
  })
  var windowSrc = src.locked_window || {}
  var lockedWindow = {
    count: typeof windowSrc.count === 'number' ? windowSrc.count : lockedCount,
    start: DATE.test(windowSrc.start || '') ? windowSrc.start : '',
    end: DATE.test(windowSrc.end || '') ? windowSrc.end : ''
  }
  return {
    program_name: String(src.program_name || ''),
    outcome: src.outcome || 'miss',
    summary_only_count: count(src.summary_only_count),
    summary_unlocks_by_product: require('./summary-unlock').counts(src.summary_unlocks_by_product, Math.min(count(src.summary_only_count), count(src.summary_hidden_count))),
    summary_hidden_count: Math.min(count(src.summary_only_count), count(src.summary_hidden_count)),
    platform_count: typeof src.platform_count === 'number' ? src.platform_count : 0,
    unverified_source_count: typeof src.unverified_source_count === 'number' ? src.unverified_source_count : 0,
    locked_summary: {
      locked_total: summary.locked_total || 0,
      by_required_tier: {
        member: tier.member || 0,
        vip: tier.vip || 0,
        evidence_only: tier.evidence_only || 0
      },
      unlockable_total: summary.unlockable_total || 0,
      cheapest_full_unlock: summary.cheapest_full_unlock || null,
      unlocks_by_product: summary.unlocks_by_product || {}
    },
    records: records,
    evidence_program_name: typeof src.evidence_program_name === 'string' ? src.evidence_program_name : undefined,
    evidence_platforms: Array.isArray(src.evidence_platforms)
      ? src.evidence_platforms.filter(function (name) { return typeof name === 'string' && name && name !== '其他平台' && name !== '平台未识别' })
      : undefined,
    locked_window: lockedWindow,
    example: !!src.example,
    viewer_tier: ['free', 'regular', 'vip'].indexOf(src.viewer_tier) === -1 ? '' : src.viewer_tier,
    bands: sanitizeBands(src.bands),
    frames_ready: src.frames_ready === true,
    report_ready: src.report_ready === true
  }
}

module.exports = {
  imageUrl: imageUrl,
  summaryHitLine: summaryHitLine,
  emptySummary: emptySummary,
  sanitizeSearch: sanitizeSearch,
  sanitizeBands: sanitizeBands
}
