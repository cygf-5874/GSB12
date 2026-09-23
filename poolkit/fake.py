"""测试用的假连接与假工厂。"""

import itertools
import threading
import time


class FakeConnection:
    """记录自己的创建序号；``close()`` 之后 ``closed`` 变 True。"""

    _counter = itertools.count(1)
    _lock = threading.Lock()

    def __init__(self, label=None, delay=0.0):
        with FakeConnection._lock:
            self.serial = next(FakeConnection._counter)
        self.label = label or "conn-%d" % self.serial
        self.delay = delay
        self.closed = False

    def close(self):
        self.closed = True

    def __repr__(self):
        return "<FakeConnection %s%s>" % (
            self.label,
            " closed" if self.closed else "",
        )


def make_factory(delay=0.0, failures=0):
    """造一个连接工厂。

    ``delay``：每次创建前 sleep 这么久，用来模拟「建连接很慢」。
    ``failures``：前 N 次调用直接抛异常。
    """
    state = {"calls": 0, "created": []}

    def factory():
        state["calls"] += 1
        if delay:
            time.sleep(delay)
        if state["calls"] <= failures:
            raise ConnectionError("创建连接失败（第 %d 次）" % state["calls"])
        conn = FakeConnection(delay=delay)
        state["created"].append(conn)
        return conn

    factory.state = state
    return factory
