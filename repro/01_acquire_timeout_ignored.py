"""缺陷 1：acquire(timeout=...) 的等待忽略 timeout，会无限挂住。

保证 2：acquire 最多等 timeout 秒，就一定在这个时间附近返回（成功或抛异常）。
复现：池满时 acquire(timeout=0.3)，若 2 秒后还没返回即判定缺陷存在。
"""
import os
import sys
import threading
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from poolkit import Pool, PoolExhausted
from poolkit.fake import make_factory

TIMEOUT = 0.3
JOIN_LIMIT = 2.0  # 远超 TIMEOUT，足够判定「没有按超时返回」


def main():
    pool = Pool(make_factory(), max_size=1)
    held = pool.acquire()  # 占满唯一的连接，之后永不释放

    outcome = {}

    def worker():
        start = time.monotonic()
        try:
            pool.acquire(timeout=TIMEOUT)
            outcome["result"] = ("got-connection", time.monotonic() - start)
        except PoolExhausted:
            outcome["result"] = ("PoolExhausted", time.monotonic() - start)
        except Exception as exc:  # noqa: BLE001
            outcome["result"] = ("unexpected:%r" % (exc,), time.monotonic() - start)

    thread = threading.Thread(target=worker, daemon=True)
    thread.start()
    thread.join(JOIN_LIMIT)

    if thread.is_alive():
        print("[FAIL] acquire(timeout=%.1f) 已阻塞超过 %.1f 秒仍未返回，"
              "timeout 被完全忽略（保证 2 不成立）" % (TIMEOUT, JOIN_LIMIT))
        return 1

    result, elapsed = outcome["result"]
    print("[INFO] acquire 返回：%s，耗时 %.3fs（约定超时 %.1fs）" % (result, elapsed, TIMEOUT))
    if result == "PoolExhausted" and elapsed <= TIMEOUT + 0.2:
        print("[OK] acquire 按约定在超时附近抛出 PoolExhausted")
        return 0
    print("[FAIL] acquire 未在超时附近按约定抛 PoolExhausted")
    return 1


if __name__ == "__main__":
    sys.exit(main())
