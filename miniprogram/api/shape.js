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

function safeUrl(value) {
  if (!value) return undefined
  var text = String(value)
  if (text.indexOf('signed://derived/') !== 0) return undefined
  return text
}

function sanitizeSearch(body) {
  var src = body || {}
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
    var open = {
      record_id: String(rec.record_id || ''),
      age_bucket: String(rec.age_bucket || 'ge_180'),
      locked: false,
      access: rec.access || 'lowres',
      can_download_report: !!rec.can_download_report,
      can_download_screenshot: !!rec.can_download_screenshot
    }
    var category = copyText(rec, 'category')
    var subtype = copyText(rec, 'platform_subtype')
    if (category) open.category = category
    if (subtype) open.platform_subtype = subtype
    var platform = copyText(rec, 'platform')
    var network = copyText(rec, 'network_type')
    var checked = copyText(rec, 'checked_at')
    var thumb = safeUrl(rec.thumbnail_url)
    var hd = safeUrl(rec.hd_url)
    if (platform) open.platform = platform
    if (network) open.network_type = network
    if (typeof rec.frame_count === 'number') open.frame_count = rec.frame_count
    if (checked) open.checked_at = checked
    if (thumb) open.thumbnail_url = thumb
    if (hd) open.hd_url = hd
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
    locked_window: lockedWindow,
    example: !!src.example
  }
}

module.exports = {
  emptySummary: emptySummary,
  sanitizeSearch: sanitizeSearch
}
