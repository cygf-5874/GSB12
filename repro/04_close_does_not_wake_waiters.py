"""缺陷 4：close() 不 notify_all，等待中的 acquire 永远醒不来。

保证 3：close() 之后，所有正在等待的 acquire 调用都会得到 PoolClosed。
复现：池满，线程 T 阻塞在 acquire()；主线程 close() 后 T 应在短时间内
抛 PoolClosed，若 1 秒后仍挂住即判定缺陷存在。
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
    held = pool.acquire()  # 占满池子

    outcome = {}

    def waiter():
        try:
            pool.acquire()  # 池满，进入等待
            outcome["result"] = "got-connection"
        except PoolClosed:
            outcome["result"] = "PoolClosed"
        except Exception as exc:  # noqa: BLE001
            outcome["result"] = "unexpected:%r" % (exc,)

    thread = threading.Thread(target=waiter, daemon=True)
    thread.start()
    time.sleep(0.1)  # 确保 T 已进入 wait

    pool.close()
    thread.join(JOIN_LIMIT)

    if thread.is_alive():
        print("[FAIL] close() 已返回，但等待中的 acquire 在 %.1f 秒后仍未收到"
              " PoolClosed（线程仍挂住，保证 3 不成立）" % JOIN_LIMIT)
        return 1

    result = outcome["result"]
    print("[INFO] 等待中的 acquire 结果：%s" % result)
    if result == "PoolClosed":
        print("[OK] close() 后等待者按约定收到 PoolClosed")
        return 0
    print("[FAIL] 等待者未收到 PoolClosed")
    return 1


if __name__ == "__main__":
    sys.exit(main())
