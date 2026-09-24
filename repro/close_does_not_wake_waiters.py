#!/usr/bin/env python3
"""缺陷复现：close() 不唤醒等待中的 acquire，优雅退出时线程永远挂在等连接上。

对应 README 对外保证 3：
  close() 之后，所有正在等待的 acquire 调用都会得到 PoolClosed。

Pool.close() 只置 _closed、关掉空闲连接，既没有 notify，唤醒后的 acquire
也不复查 _closed。本脚本构造「池满、一个 acquire 正在等」的场景，然后调用
close()：0.8 秒后等待线程仍阻塞在 poolkit 的 Condition.wait 上即判定缺陷，
退出码 1。
"""

import os
import sys
import threading
import time
import traceback

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from poolkit import Pool, PoolClosed  # noqa: E402
from poolkit.fake import make_factory  # noqa: E402

WATCH_AFTER = 0.8


def stack_of(thread):
    frame = sys._current_frames().get(thread.ident)
    return "".join(traceback.format_stack(frame)) if frame else "<no frame>"


def main():
    pool = Pool(make_factory(), max_size=1)
    held = pool.acquire()  # 占满

    outcome = {}

    def waiter():
        started = time.monotonic()
        try:
            conn = pool.acquire()
            outcome["kind"] = "acquired"
            outcome["elapsed"] = time.monotonic() - started
            outcome["conn"] = conn
        except BaseException as exc:
            outcome["kind"] = type(exc).__name__
            outcome["elapsed"] = time.monotonic() - started

    th = threading.Thread(target=waiter, name="acquire-waiter", daemon=True)
    th.start()
    time.sleep(0.2)
    print("等待线程已阻塞，调用 close() ...")
    pool.close()
    print("close() 已返回，pool.closed =", pool.closed)

    th.join(WATCH_AFTER)
    if th.is_alive() and not outcome:
        print("BUG: close() 后 %.1f 秒，等待中的 acquire 仍未被唤醒" % WATCH_AFTER)
        print("--- 等待线程的调用栈（卡在 poolkit 内部）---")
        print(stack_of(th))
        print("VERDICT: BUG REPRODUCED - close 不唤醒等待者，优雅退出会卡住")
        # 解除阻塞只用于脚本自身收尾；真实服务里没有这个动作，线程会挂到进程结束
        pool.release(held)
        return 1

    if outcome.get("kind") == "PoolClosed":
        print("OK: 等待线程在 close 后 %.2f 秒得到 PoolClosed" % outcome.get("elapsed", 0.0))
        return 0

    print("UNEXPECTED outcome: %s" % outcome)
    return 1


if __name__ == "__main__":
    sys.exit(main())
