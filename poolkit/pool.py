"""一个最小的连接池。

真实项目里池里装的是数据库连接 / socket。这里把「怎么造一个连接」交给调用方
注入的工厂函数，方便测试。
"""

import threading


class PoolError(Exception):
    """本模块所有异常的基类。"""


class PoolExhausted(PoolError):
    """在给定的超时时间内没能拿到连接。"""


class PoolClosed(PoolError):
    """池已经关闭。"""


class Pool:
    """固定上限的连接池。

    用法::

        pool = Pool(create_connection, max_size=8)
        conn = pool.acquire(timeout=1.0)
        try:
            use(conn)
        finally:
            pool.release(conn)

    约定：

    - 池里同时存在的连接数不超过 ``max_size``；
    - ``acquire`` 在池满时最多等 ``timeout`` 秒，等不到抛 :class:`PoolExhausted`；
      ``timeout=None`` 表示一直等；
    - 池关闭后 ``acquire`` 抛 :class:`PoolClosed`；
    - ``release`` 把连接还回空闲列表，重复 release 同一个连接是幂等的。
    """

    def __init__(self, factory, max_size=4):
        if not callable(factory):
            raise TypeError("factory 必须可调用")
        if isinstance(max_size, bool) or not isinstance(max_size, int) or max_size <= 0:
            raise ValueError("max_size 必须是正整数")

        self._factory = factory
        self._max_size = max_size
        self._idle = []
        self._live = 0
        self._closed = False
        self._cond = threading.Condition()

    # -- 只读状态 -----------------------------------------------------

    @property
    def max_size(self):
        return self._max_size

    @property
    def live_count(self):
        """已创建且未关闭的连接数（含正在被占用的）。"""
        with self._cond:
            return self._live

    @property
    def idle_count(self):
        """当前空闲的连接数。"""
        with self._cond:
            return len(self._idle)

    @property
    def closed(self):
        with self._cond:
            return self._closed

    # -- 取还 ---------------------------------------------------------

    def acquire(self, timeout=None):
        """取一个连接。池满时最多等 ``timeout`` 秒。"""
        with self._cond:
            if self._closed:
                raise PoolClosed("连接池已关闭")
            if self._idle:
                return self._idle.pop()
            if self._live < self._max_size:
                self._live += 1
                return self._factory()
            # 池满，等别人还回来
            self._cond.wait()
            if self._idle:
                return self._idle.pop()
            raise PoolExhausted("等待连接超时")

    def release(self, conn):
        """把连接还回池子。"""
        with self._cond:
            if conn in self._idle:
                return
            self._idle.append(conn)
            self._cond.notify()

    def close(self):
        """关闭池，并关掉当前空闲的连接。"""
        with self._cond:
            self._closed = True
            for conn in self._idle:
                conn.close()
            self._idle.clear()
