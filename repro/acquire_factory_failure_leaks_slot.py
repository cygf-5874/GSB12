#!/usr/bin/env python3
"""缺陷复现：工厂建连失败时 _live 已 +1 却不回滚，池子「假满」。

Pool.acquire 在持锁状态下先执行 self._live += 1，再调用 self._factory()；
工厂抛异常时计数不会回滚。此后池里实际一条连接都没有，live_count 却等于
max_size，后续 acquire 被当成「池满」丢去等待 —— 高峰期工厂偶发失败时就是
线上看到的「明明没几个连接在用，acquire 却一直拿不到」。

判定（两个条件同时成立才算）：
1. 工厂失败后 live_count == max_size，而 idle_count == 0、调用方手里也没有连接；
2. 之后的 acquire(timeout=0.3) 在 0.9 秒后仍阻塞。
满足则退出码 1。
"""

import os
import sys
import threading
import time
import traceback

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from poolkit import Pool  # noqa: E402
from poolkit.fake import make_factory  # noqa: E402

MAX_SIZE = 1
TIMEOUT = 0.3
WATCH_AFTER = TIMEOUT + 0.6


def stack_of(thread):
    frame = sys._current_frames().get(thread.ident)
    return "".join(traceback.format_stack(frame)) if frame else "<no frame>"


def main():
    factory = make_factory(failures=1)  # 第 1 次造连接抛 ConnectionError，之后成功
    pool = Pool(factory, max_size=MAX_SIZE)

    try:
        pool.acquire()
        print("UNEXPECTED: 工厂本该失败却成功了")
        return 1
    except ConnectionError as exc:
        print("第一次 acquire 触发工厂失败，异常透传给调用方: %r" % exc)

    live = pool.live_count
    idle = pool.idle_count
    print("工厂失败后: live_count=%d, idle_count=%d, 调用方持有连接=0" % (live, idle))
    accounting_broken = (live == MAX_SIZE and idle == 0)

    # 池里根本没有连接，按理下一次 acquire 应该立刻造一条新连接
    outcome = {}

    def waiter():
        started = time.monotonic()
        try:
            conn = pool.acquire(timeout=TIMEOUT)
            outcome["kind"] = "acquired"
            outcome["elapsed"] = time.monotonic() - started
            outcome["conn"] = conn
        except BaseException as exc:
            outcome["kind"] = type(exc).__name__
            outcome["elapsed"] = time.monotonic() - started

    th = threading.Thread(target=waiter, daemon=True)
    th.start()
    th.join(WATCH_AFTER)
    hung = th.is_alive() and not outcome

    if accounting_broken and hung:
        print("BUG: 实际空闲，但 live_count 已到上限，acquire(timeout=%.1f) 在 %.1f 秒后仍阻塞"
              % (TIMEOUT, WATCH_AFTER))
        print("--- 等待线程的调用栈 ---")
        print(stack_of(th))
        print("VERDICT: BUG REPRODUCED - 工厂失败泄漏槽位，连接池假满")
        return 1

    if accounting_broken:
        print("计数已被污染（live_count=%d），但等待行为与预期不符: %s" % (live, outcome))
        return 1

    if hung:
        print("UNEXPECTED: 计数正常却阻塞: %s" % outcome)
        return 1

    print("OK: 工厂失败后 live_count=%d，后续 acquire %.2f 秒内返回(%s)"
          % (live, outcome.get("elapsed", 0.0), outcome.get("kind")))
    return 0


if __name__ == "__main__":
    sys.exit(main())
