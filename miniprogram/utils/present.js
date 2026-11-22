var catalog = require('../api/catalog')

function findRow(id) {
  var found = null
  Object.keys(catalog.programs).forEach(function (name) {
    catalog.programs[name].rows.forEach(function (row) {
      if (row.id === id) found = row
    })
  })
  return found
}

function toRow(rec, programName, mock) {
  var extra = mock ? (findRow(rec.record_id) || {}) : {}
  var platform = rec.platform || extra.platform
  var unknown = !platform || platform === '平台未识别'
  return {
    id: rec.record_id,
    mock: mock,
    image: /^https:\/\//.test(rec.thumbnail_url || '') ? rec.thumbnail_url : '',
    title: extra.title || programName,
    platform: unknown ? '平台未识别' : platform,
    unknown: unknown,
    sub: unknown ? '' : (rec.platform_subtype || extra.sub || ''),
    category: rec.category || '',
    time: (rec.timestamps || [])[0] || extra.time || '',
    clock: rec.checked_at ? rec.checked_at.slice(11, 16) : extra.clock || '',
    date: rec.checked_at ? rec.checked_at.slice(0, 10) : extra.date || '',
    badge: extra.low || rec.access === 'lowres' ? '低清' : '高清',
    low: !!(extra.low || rec.access === 'lowres'),
    canSave: !!rec.can_download_screenshot && rec.access !== 'lowres' && !extra.low
  }
}

function groupRows(rows) {
  var groups = []
  rows.forEach(function (row) {
    var last = groups.length ? groups[groups.length - 1] : null
    if (!last || last.platform !== row.platform) {
      groups.push({ platform: row.platform, unknown: row.unknown, rows: [row] })
    } else last.rows.push(row)
  })
  return groups
}

function present(search, meta) {
  var info = meta || {}
  var name = info.name || search.program_name || ''
  var opens = []
  var evidence = 0
  var locked = 0
  ;(search.records || []).forEach(function (rec) {
    if (rec.locked) {
      if (rec.age_bucket === 'lt_30') evidence += 1
      else locked += 1
      return
    }
    opens.push(toRow(rec, name, !!search.example || require('../config').mode === 'mock'))
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
  var memberText = info.expired ? '续费会员 · ¥299/30天起' : '开通会员 · ¥299/30天起'
  var note = ''

  return {
    name: name,
    example: !!search.example,
    badge: search.outcome === 'miss' ? '未命中' : '已命中',
    hit: search.outcome === 'hit',
    miss: search.outcome === 'miss',
    header: header,
    platformCount: search.platform_count,
    platformLine: '涉及 ' + search.platform_count + ' 个平台',
    showOpen: showOpen,
    groups: showOpen ? groupRows(opens) : [],
    showLocked: showLocked,
    lockedCount: lockedCount,
    lockedRange: range,
    recordLine: '记录 ' + lockedCount + ' 条 · 涉及 ' + search.platform_count + ' 个平台',
    unlockText: '解锁后查看 ' + lockedCount + ' 条记录',
    fakeRows: [{ id: 'a' }, { id: 'b' }, { id: 'c' }],
    memberText: memberText,
    singleText: '单节目解锁（含 30 天以上）¥199',
    showEvidence: evidence > 0,
    showDownload: showOpen && !search.example && !!info.canExport,
    showHint: showLocked && !info.expired,
    expired: !!info.expired,
    note: note
  }
}

module.exports = { present: present, findRow: findRow }
