# poolkit

一个固定上限的连接池。连接怎么造由调用方注入的 `factory` 决定（真实项目里是
数据库连接 / socket），测试和复现脚本用 `poolkit.fake.make_factory()` 造假的。

零第三方依赖，Python 3.9 以上直接跑：

```bash
python3 -m unittest discover -s tests -v
```

## 公开 API 与约定

```python
from poolkit import Pool, PoolClosed, PoolExhausted
from poolkit.fake import make_factory

pool = Pool(make_factory(), max_size=4)
conn = pool.acquire(timeout=1.0)
try:
    use(conn)
finally:
    pool.release(conn)
```

| 接口 | 说明 |
| --- | --- |
| `Pool(factory, max_size=4)` | `factory` 不可调用抛 `TypeError`；`max_size` 非正整数抛 `ValueError`。 |
| `Pool.acquire(timeout=None)` | 取一个连接。池满时最多等 `timeout` 秒，等不到抛 `PoolExhausted`；`timeout=None` 表示一直等。池已关闭抛 `PoolClosed`。 |
| `Pool.release(conn)` | 把连接还回空闲列表，重复 release 同一个连接幂等。 |
| `Pool.close()` | 关闭池并关掉当前空闲的连接（正在被占用的不动），可重复调用。 |
| `Pool.live_count` | 已创建且未关闭的连接数（含正在被占用的）。 |
| `Pool.idle_count` | 当前空闲的连接数。 |
| `Pool.max_size` / `Pool.closed` | 只读属性。 |

**对外保证**：

1. 池里同时存在的连接数不超过 `max_size`；
2. `acquire` 说好最多等 `timeout` 秒，就一定在这个时间附近返回（成功或抛异常），
   不会无限期挂住；
3. `close()` 之后，所有正在等待的 `acquire` 调用都会得到 `PoolClosed`。

## 目录

```
poolkit/
  pool.py     Pool 实现
  fake.py     假连接 / 假工厂（make_factory(delay=, failures=)）
tests/        既有用例，覆盖单线程路径
```
