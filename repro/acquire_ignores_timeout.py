#!/usr/bin/env python3
"""缺陷复现：Pool.acquire 完全忽略 timeout，池满时无限等待。

对应 README 对外保证 2：
  acquire 说好最多等 timeout 秒，就一定在这个时间附近返回（成功或抛异常）。

判定：池满时以 timeout=0.3 调用 acquire，0.9 秒后线程仍阻塞在
poolkit 的 Condition.wait 上 => 缺陷复现，退出码 1。
"""

import os
import sys
import threading
import time
import traceback

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from poolkit import Pool, PoolExhausted  # noqa: E402
from poolkit.fake import make_factory  # noqa: E402

TIMEOUT = 0.3
WATCH_AFTER = TIMEOUT + 0.6


def stack_of(thread):
    frame = sys._current_frames().get(thread.ident)
    if frame is None:
        return "<no frame>"
    return "".join(traceback.format_stack(frame))


def main():
    pool = Pool(make_factory(), max_size=1)
    held = pool.acquire()  # 唯一连接被本线程占住，池子真满

    outcome = {}

    def waiter():
        started = time.monotonic()
        try:
            conn = pool.acquire(timeout=TIMEOUT)
            outcome["kind"] = "acquired"
            outcome["elapsed"] = time.monotonic() - started
            outcome["conn"] = conn
        except BaseException as exc:  # 记录任何返回方式
            outcome["kind"] = type(exc).__name__
            outcome["elapsed"] = time.monotonic() - started

    th = threading.Thread(target=waiter, name="acquire-waiter", daemon=True)
    th.start()
    th.join(WATCH_AFTER)

    if th.is_alive() and not outcome:
        print("BUG: acquire(timeout=%.1f) 在 %.1f 秒后仍未返回" % (TIMEOUT, WATCH_AFTER))
        print("     此时 live_count=%d idle_count=%d" % (pool.live_count, pool.idle_count))
        print("--- 等待线程的调用栈 ---")
        print(stack_of(th))

        # 再证明它不是“快超时了”，而是根本没计时：直到有人 release 才返回成功
        pool.release(held)
        th.join(1.0)
        print("release 后等待线程才返回: %s，实际耗时 %.2f 秒（拿到连接而非 PoolExhausted）"
              % (outcome.get("kind"), outcome.get("elapsed", -1)))
        print("VERDICT: BUG REPRODUCED - timeout 参数被丢弃，acquire 无限等待")
        return 1

    kind = outcome.get("kind")
    elapsed = outcome.get("elapsed", 0.0)
    if kind == "PoolExhausted" and elapsed <= WATCH_AFTER:
        print("OK: acquire(timeout=%.1f) 在 %.2f 秒后抛出 PoolExhausted" % (TIMEOUT, elapsed))
        pool.release(held)
        return 0

    print("UNEXPECTED: kind=%s elapsed=%.2f" % (kind, elapsed))
    pool.release(held)
    return 1


if __name__ == "__main__":
    sys.exit(main())
