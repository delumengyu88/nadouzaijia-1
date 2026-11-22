var imageUrl = require('../api/shape').imageUrl
var beijingTime = require('./date').beijingTime
var OTHER = '其他平台'
var AGE_LABELS = {
  lt_30: '30天内',
  d30_89: '30–89天',
  d90_179: '90–179天',
  ge_180: '180天以上'
}

function ageLabelOf(bucket, publicExample) {
  if (!publicExample || bucket == null || bucket === '' || bucket === 'unknown') return ''
  return AGE_LABELS[bucket] || ''
}

function findRow(id) {
  var catalog = require('../api/catalog')
  var found = null
  Object.keys(catalog.programs).forEach(function (name) {
    catalog.programs[name].rows.forEach(function (row) {
      if (row.id === id) found = row
    })
  })
  return found
}

function toRow(rec, programName, mock, publicExample) {
  var extra = mock ? (findRow(rec.record_id) || {}) : {}
  var platform = rec.platform || extra.platform
  var unknown = !platform || platform === OTHER || platform === '平台未识别'
  var image = imageUrl(rec.thumbnail_url) || (rec.access === 'full' && imageUrl(rec.hd_url)) || ''
  var checked = beijingTime(rec.checked_at)
  var badge = ''
  if (!(publicExample && (rec.access == null || rec.access === ''))) {
    badge = extra.low || rec.access === 'lowres' ? '低清' : (mock || imageUrl(rec.hd_url) ? '高清' : '')
  }
  return {
    id: rec.record_id,
    mock: mock,
    plainThumb: !mock && !image,
    image: image,
    title: extra.title || programName,
    platform: unknown ? OTHER : platform,
    unknown: unknown,
    sub: unknown ? '' : (rec.platform_subtype || extra.sub || ''),
    category: rec.category || '',
    time: (rec.timestamps || [])[0] || extra.time || '',
    clock: checked ? checked.slice(11, 16) : (publicExample ? '' : (extra.clock || '')),
    date: checked ? checked.slice(0, 10) : (publicExample ? '' : (extra.date || '')),
    ageLabel: ageLabelOf(rec.age_bucket, publicExample),
    badge: badge,
    low: !!(extra.low || rec.access === 'lowres'),
    canSave: !!rec.can_download_screenshot && rec.access !== 'lowres' && !extra.low
  }
}

// 其他平台 always goes last on P08; the rest keep server order (stable sort).
function otherLast(rows) {
  var known = rows.filter(function (row) { return !row.unknown })
  return known.concat(rows.filter(function (row) { return row.unknown }))
}

function groupRows(rows) {
  var groups = []
  otherLast(rows).forEach(function (row) {
    var last = groups.length ? groups[groups.length - 1] : null
    if (!last || last.platform !== row.platform) {
      groups.push({ platform: row.platform, unknown: row.unknown, rows: [row] })
    } else last.rows.push(row)
  })
  return groups
}

// Live searches carry the server list, which never includes generated-date rows.
function evidencePlatforms(search, opens) {
  if (Array.isArray(search.evidence_platforms)) return search.evidence_platforms.slice()
  var names = []
  opens.forEach(function (row) {
    if (!row.unknown && names.indexOf(row.platform) === -1) names.push(row.platform)
  })
  return names
}

function present(search, meta) {
  var info = meta || {}
  var name = info.name || search.program_name || ''
  var opens = []
  var evidence = 0
  var locked = 0
  var publicExample = !!search.publicExample
  var catalogMock = !publicExample && require('../config').mode === 'mock'
  ;(search.records || []).forEach(function (rec) {
    if (rec.locked) {
      if (rec.age_bucket === 'lt_30') evidence += 1
      else locked += 1
      return
    }
    opens.push(toRow(rec, name, catalogMock, publicExample))
  })
  var window = search.locked_window || {}
  var lockedCount = window.count || locked
  var range = window.start && window.end ? window.start + ' 至 ' + window.end : ''
  var showOpen = opens.length > 0 && !info.forceLocked
  var showLocked = lockedCount > 0 && !info.forceOpen
  var header = ''
  if (showOpen) {
    header = '命中 ' + (opens.length + lockedCount) + ' 条 · 涉及 ' + search.platform_count + ' 个平台'
  } else if (info.queried) {
    header = '查询于 ' + info.queried
  }
  var memberText = info.expired ? '续费会员' : '开通会员'
  if (catalogMock) memberText += ' · ¥299/30天起'
  var note = ''

  return {
    name: name,
    example: !!search.example,
    badge: search.outcome === 'miss' ? '未命中' : '已命中',
    badgeCls: search.outcome === 'miss' ? 'miss' : 'ok',
    hit: search.outcome === 'hit',
    miss: search.outcome === 'miss',
    header: header,
    platformCount: search.platform_count,
    platformLine: '涉及 ' + search.platform_count + ' 个平台',
    showOpen: showOpen,
    groups: showOpen ? groupRows(opens) : [],
    evidencePlatforms: evidencePlatforms(search, opens),
    evidenceName: typeof search.evidence_program_name === 'string' ? search.evidence_program_name : name,
    showLocked: showLocked,
    lockedCount: lockedCount,
    lockedRange: range,
    recordLine: '记录 ' + lockedCount + ' 条 · 涉及 ' + search.platform_count + ' 个平台',
    unlockText: '解锁后查看 ' + lockedCount + ' 条记录',
    fakeRows: [{ id: 'a' }, { id: 'b' }, { id: 'c' }].slice(0, catalogMock ? 3 : lockedCount),
    showMemberOffer: catalogMock,
    showSingleOffer: catalogMock,
    memberText: memberText,
    singleText: catalogMock ? '单节目解锁（含 30 天以上）¥199' : '单节目解锁',
    showEvidence: evidence > 0,
    showDownload: showOpen && !search.example && !!info.canExport,
    showHint: showLocked && !info.expired,
    expired: !!info.expired,
    note: note
  }
}

function exampleFacts(records) {
  var rows = (records || []).filter(function (rec) { return rec && rec.locked !== true })
  var platforms = []
  var latest = ''
  var image = ''
  rows.forEach(function (rec) {
    var platform = rec.platform
    if (platform && platform !== OTHER && platform !== '平台未识别' && platforms.indexOf(platform) === -1) platforms.push(platform)
    var day = beijingTime(rec.checked_at).slice(0, 10)
    if (/^\d{4}-\d{2}-\d{2}$/.test(day) && day > latest) latest = day
    if (!image) image = imageUrl(rec.thumbnail_url) || (rec.access === 'full' && imageUrl(rec.hd_url)) || ''
  })
  var sub = '命中 ' + rows.length + ' 条记录'
  if (latest) sub += ' · ' + latest
  return { line: '涉及 ' + platforms.length + ' 个平台', sub: sub, hit: rows.length > 0, image: image }
}

module.exports = { present: present, findRow: findRow, otherLast: otherLast, exampleFacts: exampleFacts, OTHER: OTHER }
