var assert = require('assert')
var config = require('../miniprogram/config')
config.apiBase = '/test-api'
var calls = []
global.wx = {
  getStorageSync: function () { return null },
  setStorageSync: function () {},
  request: function (opt) { calls.push(opt) }
}
var state = require('../miniprogram/api/state')
var request = require('../miniprogram/api/request')

function drain() { return new Promise(function (resolve) { setImmediate(resolve) }) }

async function main() {
  state.reset()
  var blocked = request('/api/v1/invite-codes/state')
  var blockedError = blocked.then(function () { return null }, function (error) { return error })
  await drain()
  assert.strictEqual(calls.length, 0, 'a missing token does not send a normal request')
  assert.strictEqual((await blockedError).message, '请先登录')

  var guest = request('/api/v1/invite-codes/state', {}, { optionalAuth: true })
  await drain()
  assert.strictEqual(calls.length, 1, 'optionalAuth sends the request without a token')
  assert.strictEqual(calls[0].header.Authorization, undefined)
  calls.shift().success({ statusCode: 200, data: { guest: true, bound: false, bindText: '兑换邀请码' } })
  assert.deepStrictEqual(await guest, { guest: true, bound: false, bindText: '兑换邀请码' })

  state.setSession({
    token: 'test-token',
    expires_at: new Date(Date.now() + 86400000).toISOString(),
    user: { id: 'user-1', tier: 'free' }
  })
  var body = request('/api/v1/evidence-requests', {}, { method: 'POST', readBody: true })
  await drain()
  assert.strictEqual(calls[0].header.Authorization, 'Bearer test-token')
  calls.shift().success({ statusCode: 409, data: { code: 'duplicate', id: 'e1' } })
  assert.deepStrictEqual(await body, { code: 'duplicate', id: 'e1' })

  var exposed = request('/api/v1/invite-codes/redeem', { code: 'ABCD2345' }, { method: 'POST', exposeStatus: true })
  await drain()
  calls.shift().success({ statusCode: 400, data: { detail: 'invalid' } })
  assert.deepStrictEqual(await exposed, { statusCode: 400, data: { detail: 'invalid' } })

  console.log('request options check ok')
}

main().catch(function (error) {
  console.error(error && error.stack ? error.stack : error)
  process.exit(1)
})
