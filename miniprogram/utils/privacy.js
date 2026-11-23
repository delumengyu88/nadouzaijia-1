var handlers = []
var pending = []

function notify(resolve) {
  pending.push(resolve)
  if (!handlers.length) return
  handlers[handlers.length - 1]()
}

function subscribe(handler) {
  handlers.push(handler)
  if (pending.length) handler()
  return function unsubscribe() {
    handlers = handlers.filter(function (item) { return item !== handler })
    if (pending.length && handlers.length) handlers[handlers.length - 1]()
  }
}

function consume() {
  return pending.splice(0, pending.length)
}

module.exports = {
  notify: notify,
  subscribe: subscribe,
  consume: consume
}
