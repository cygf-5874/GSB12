"""固定上限的连接池。

只依赖标准库。
"""

from .pool import Pool, PoolClosed, PoolError, PoolExhausted

__all__ = ["Pool", "PoolError", "PoolExhausted", "PoolClosed"]

__version__ = "0.6.2"
