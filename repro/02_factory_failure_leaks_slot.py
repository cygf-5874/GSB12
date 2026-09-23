"""缺陷 2：factory 抛异常后 _live 不回滚，名额泄漏导致「假满」。

复现：max_size=1，第一次 acquire 时 factory 失败。之后池里实际 0 个连接，
若 live_count 仍显示 1、且后续 acquire 进池满等待路径（挂住），即判定缺陷存在。
"""
import os
import sys
import threading
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from poolkit import Pool, PoolExhausted
from poolkit.fake import make_factory

JOIN_LIMIT = 1.0


def main():
    factory = make_factory(failures=1)  # 第一次创建必失败
    pool = Pool(factory, max_size=1)

    try:
        pool.acquire()
        print("[FAIL] 第一次 acquire 应当抛 ConnectionError（factory 失败）")
        return 1
    except ConnectionError:
        pass

    created = len(factory.state["created"])
    live = pool.live_count
    idle = pool.idle_count
    print("[INFO] factory 实际创建连接数 = %d，live_count = %d，idle_count = %d"
          % (created, live, idle))

    # 池里实际一个连接都没有，第二次 acquire 本应立刻新建成功
    outcome = {}

    def worker():
        try:
            outcome["conn"] = pool.acquire(timeout=0.2)
        except Exception as exc:  # noqa: BLE001
            outcome["error"] = exc

    thread = threading.Thread(target=worker, daemon=True)
    thread.start()
    thread.join(JOIN_LIMIT)

    leaked = live != created  # 没有任何活连接，live_count 却不为 0
    stuck = thread.is_alive() or "error" in outcome
    if leaked or stuck:
        print("[FAIL] factory 失败后名额未回滚：池里 0 个真实连接，"
              "live_count 却为 %d（假满）" % live)
        if thread.is_alive():
            print("[FAIL] 后续 acquire 在空池上进入池满等待，%.1f 秒未返回" % JOIN_LIMIT)
        elif "error" in outcome:
            print("[FAIL] 后续 acquire 在空池上抛了 %r" % (outcome["error"],))
        return 1

    print("[OK] factory 失败后名额已回滚，后续 acquire 正常拿到连接")
    return 0


if __name__ == "__main__":
    sys.exit(main())
