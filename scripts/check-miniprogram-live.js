var assert = require('assert')
var config = require('../miniprogram/config')
config.calibrating = false
assert.strictEqual(config.mode, 'hybrid')
assert.strictEqual(config.apiBase, '')
config.apiBase = '/test-api'
var calls = []
var nextError = false
var store = {}
global.wx = {
  getStorageSync: function (k) { return store[k] }, setStorageSync: function (k, v) { store[k] = v },
  getSystemInfoSync: function () { return { statusBarHeight: 20 } },
  request: function (opt) {
    calls.push(opt)
    if (nextError) { nextError = false; opt.fail(); return }
    opt.success({ statusCode: 200, data: { program_name: opt.data.name, outcome: 'hit', platform_count: 1,
      platform_groups: [{ records: [{ record_id: 'xh1', locked: false, platform: '真实测试平台', category: '测试类别',
        access: 'full', checked_at: '2026-01-02T03:04:05+08:00', can_download_report: true }] }],
      locked_records: [{ record_id: 'locked', locked: true, age_bucket: 'd30_89', platform: '禁止泄露' }],
      locked_summary: { locked_total: 1, unlockable_total: 1, by_required_tier: { vip: 1 }, cheapest_full_unlock: 'single_program_30d' }
    } })
  }
}
var api = require('../miniprogram/api')
var state = require('../miniprogram/api/state')
function drain() { return new Promise(function (resolve) { setImmediate(resolve) }) }
async function main() {
  api.reset(); api.switchAccount('free')
  assert.strictEqual(api.isLive, true)
  assert.strictEqual(api.listEvidence().length, 0)
  var query = api.createQuery(['真实测试片', '失败测试片'])
  api.advanceQuery(query.id); await drain()
  assert.strictEqual(calls[0].url, '/test-api/api/v1/programs/search')
  assert.strictEqual(calls[0].header['X-User-Id'], config.testUserIds.free)
  nextError = true; api.advanceQuery(query.id); await drain()
  var results = api.getQuery(query.id)
  assert(results.done && results.items[0].hit && results.items[1].failed)
  assert.strictEqual(results.items[0].evidencePlatform, '真实测试平台')
  api.retryItem(query.id, '失败测试片'); await drain()
  assert(api.getQuery(query.id).items[1].hit)
  api.switchAccount('member')
  assert.strictEqual(api.getQuery(query.id), null)
  var body = await api.searchProgram('真实测试片')
  assert.strictEqual(calls[calls.length - 1].header['X-User-Id'], config.testUserIds.member)
  assert.strictEqual(body.records[1].platform, undefined)
  var presented = require('../miniprogram/utils/present').present(body)
  assert.strictEqual(presented.groups[0].rows[0].platform, '真实测试平台')
  assert.strictEqual(presented.groups[0].rows[0].date, '2026-01-02')
  assert.strictEqual(presented.groups[0].rows[0].title, '真实测试片')
  assert(presented.showOpen && presented.showLocked)
  var book = await api.exportBook('真实测试片')
  var data = Buffer.from(book.bytes).toString('utf8')
  assert(data.includes('真实测试平台') && !data.includes('禁止泄露'))
  var def
  global.Page = function (p) { def = p }
  require('../miniprogram/pages/report/index')
  var page = Object.assign({}, def, { data: {}, setData: function (v) { Object.assign(this.data, v) } })
  page.query = { name: '真实测试片' }; page.load(); await drain()
  assert.strictEqual(page.data.phase, 'ready')
  assert(page.data.showDownload)
  nextError = true; page.retry(); await drain()
  assert.strictEqual(page.data.phase, 'fail')
  require('../miniprogram/pages/viewer/index')
  function viewer() {
    return Object.assign({}, def, { data: {}, setData: function (v) { Object.assign(this.data, v) } })
  }
  var shot = viewer()
  shot.onLoad({ name: '真实测试片', id: 'xh1' })
  assert.strictEqual(shot.data.loading, true)
  assert.strictEqual(shot.data.missing, false)
  await drain()
  assert.strictEqual(shot.data.loading, false)
  assert.strictEqual(shot.data.missing, false)
  assert.strictEqual(shot.data.platform, '真实测试平台')
  nextError = true
  shot = viewer(); shot.onLoad({ name: '真实测试片', id: 'xh1' })
  assert.strictEqual(shot.data.loading, true)
  assert.strictEqual(shot.data.missing, false)
  await drain()
  assert.strictEqual(shot.data.loading, false)
  assert.strictEqual(shot.data.missing, true)
  shot = viewer(); shot.onLoad({ name: '真实测试片', id: 'absent' })
  await drain()
  assert.strictEqual(shot.data.loading, false)
  assert.strictEqual(shot.data.missing, true)
  config.apiBase = ''
  await assert.rejects(api.searchProgram('真实测试片'), /未配置服务地址/)
  assert.strictEqual(state.userId(), config.testUserIds.member)
  console.log('hybrid API, identity, retry, report and export checks ok')
}
main().catch(function (error) { console.error(error); process.exitCode = 1 })
