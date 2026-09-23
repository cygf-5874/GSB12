"""缺陷 3：acquire 持锁调用 factory()，慢建连期间全池（含 close）被冻住。

复现：factory 每次创建耗时 0.6s。线程 A 进入 acquire 开始建连后，
主线程调用 close()。close 本应立刻完成，若被建连拖住（>=0.3s）即判定缺陷存在。
"""
import os
import sys
import threading
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from poolkit import Pool
from poolkit.fake import make_factory

DELAY = 0.6
THRESHOLD = 0.3  # close 若被慢建连阻塞，耗时会接近 DELAY


def main():
    pool = Pool(make_factory(delay=DELAY), max_size=2)

    creator = threading.Thread(target=pool.acquire, daemon=True)
    creator.start()
    time.sleep(0.1)  # 确保 A 已进入 acquire、正在持锁执行 factory

    start = time.monotonic()
    pool.close()
    blocked = time.monotonic() - start
    creator.join(2.0)

    print("[INFO] factory 单次创建耗时 %.1fs；close() 实际耗时 %.3fs" % (DELAY, blocked))
    if blocked >= THRESHOLD:
        print("[FAIL] close() 被一个进行中的慢建连阻塞了 %.3fs：factory 是在"
              "池锁内执行的，建连期间 release/close/acquire 全部冻结" % blocked)
        return 1

    print("[OK] close() 未被慢建连阻塞")
    return 0


if __name__ == "__main__":
    sys.exit(main())
