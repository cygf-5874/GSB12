"""缺陷 6（附带）：close() 关闭空闲连接后 _live 不减少，live_count 失真。

文档：live_count 是「已创建且未关闭的连接数（含正在被占用的）」。
复现：2 个连接全部还回后 close()，两者都被关闭，live_count 应为 0。
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from poolkit import Pool
from poolkit.fake import make_factory


def main():
    pool = Pool(make_factory(), max_size=2)
    conn1 = pool.acquire()
    conn2 = pool.acquire()
    pool.release(conn1)
    pool.release(conn2)
    pool.close()

    print("[INFO] close 后：conn1.closed=%s conn2.closed=%s live_count=%d idle_count=%d"
          % (conn1.closed, conn2.closed, pool.live_count, pool.idle_count))

    if not (conn1.closed and conn2.closed):
        print("[FAIL] close() 没有关闭空闲连接")
        return 1
    if pool.live_count != 0:
        print("[FAIL] 两个连接均已被 close() 关闭，live_count 却为 %d，"
              "与文档「已创建且未关闭的连接数」不符" % pool.live_count)
        return 1
    print("[OK] close 后 live_count 正确归零")
    return 0


if __name__ == "__main__":
    sys.exit(main())
