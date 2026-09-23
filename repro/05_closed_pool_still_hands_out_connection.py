"""缺陷 5：等待中的 acquire 醒来不复查 _closed；release 在 close 后仍把连接放回池。

保证 3：close() 之后，所有正在等待的 acquire 调用都会得到 PoolClosed。
复现：线程 T 在池满时等待；close() 之后原持有者按 try/finally 正常 release。
若 T 拿到了连接（而非 PoolClosed），或还回的连接永远没被关闭，即判定缺陷存在。
"""
import os
import sys
import threading
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from poolkit import Pool, PoolClosed
from poolkit.fake import make_factory

JOIN_LIMIT = 1.0


def main():
    pool = Pool(make_factory(), max_size=1)
    held = pool.acquire()

    outcome = {}

    def waiter():
        try:
            outcome["conn"] = pool.acquire()
        except PoolClosed:
            outcome["result"] = "PoolClosed"
        except Exception as exc:  # noqa: BLE001
            outcome["result"] = "unexpected:%r" % (exc,)

    thread = threading.Thread(target=waiter, daemon=True)
    thread.start()
    time.sleep(0.1)  # 确保 T 已进入 wait

    pool.close()
    pool.release(held)  # 调用方 finally 里的正常还连接
    thread.join(JOIN_LIMIT)

    if thread.is_alive():
        print("[FAIL] close()+release 后等待中的 acquire 仍挂住（参见缺陷 4）")
        return 1

    failed = False
    if "conn" in outcome:
        conn = outcome["conn"]
        print("[FAIL] 池已关闭（closed=%s），等待中的 acquire 却拿到了连接 %r，"
              "而不是保证 3 承诺的 PoolClosed" % (pool.closed, conn))
        failed = True
        if not conn.closed:
            print("[FAIL] 该连接是在 close() 之后才还回池的，永远不会被关闭"
                  "（conn.closed=False，连接泄漏）")
    elif outcome.get("result") == "PoolClosed":
        print("[OK] 关闭后等待者收到 PoolClosed")
    else:
        print("[FAIL] 等待者结果异常：%s" % outcome.get("result"))
        failed = True

    if not failed and held.closed:
        print("[OK] close 后还回的连接被正确关闭")
        return 0
    if not failed and not held.closed:
        print("[FAIL] close 后 release 的连接未被关闭（连接泄漏）")
        failed = True
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
