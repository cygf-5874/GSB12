#!/usr/bin/env python3
"""缺陷复现：等待中的 acquire 被唤醒后不复查池是否已关闭，会从「已关闭的池」
拿到一个未关闭的连接，而不是 README 保证的 PoolClosed。

对应 README 对外保证 3：
  close() 之后，所有正在等待的 acquire 调用都会得到 PoolClosed。

步骤：max_size=1，一个线程阻塞在 acquire；随后 close()（等待者不会被唤醒）；
占用方 release() 后等待者直接 self._idle.pop() 返回。此时：
  - 返回类型是连接而不是 PoolClosed；
  - 该连接 conn.closed 是 False（release 在 close 之后，close 没关到它）。
两项任一成立即判定缺陷，退出码 1。
"""

import os
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from poolkit import Pool, PoolClosed  # noqa: E402
from poolkit.fake import make_factory  # noqa: E402


def main():
    pool = Pool(make_factory(), max_size=1)
    held = pool.acquire()  # 占满

    outcome = {}
    done = threading.Event()

    def waiter():
        started = time.monotonic()
        try:
            conn = pool.acquire()
            outcome["kind"] = "acquired"
            outcome["conn"] = conn
        except BaseException as exc:
            outcome["kind"] = type(exc).__name__
        finally:
            outcome["elapsed"] = time.monotonic() - started
            done.set()

    th = threading.Thread(target=waiter, daemon=True)
    th.start()
    time.sleep(0.2)

    pool.close()
    print("close() 完成，pool.closed =", pool.closed, "；此时占用方释放连接")
    pool.release(held)  # close 之后才归还：close 没关到它，且会 notify 唤醒等待者

    if not done.wait(1.0):
        print("BUG: 等待线程在 release 后 1 秒仍未返回（同时违反保证 3）")
        return 1

    if outcome["kind"] == "PoolClosed":
        print("OK: 等待线程在 %.2f 秒后得到 PoolClosed" % outcome["elapsed"])
        return 0

    conn = outcome.get("conn")
    conn_closed = getattr(conn, "closed", "<n/a>")
    print("BUG: close() 之后等待的 acquire 返回了 %s 而不是 PoolClosed" % outcome["kind"])
    print("     返回对象: %r" % (conn,))
    print("     pool.closed=%s，而该连接 conn.closed=%s" % (pool.closed, conn_closed))
    print("VERDICT: BUG REPRODUCED - 关闭后仍可从池里取连接，保证 3 不成立")
    return 1


if __name__ == "__main__":
    sys.exit(main())
