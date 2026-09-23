# poolkit 并发审查报告

审查对象：`poolkit/pool.py`（未改动任何被测代码）。判定标准为 README 「公开 API 与约定」
中的三条对外保证，以及线上两类报障（连接池假满 / 优雅退出卡住）。

每条缺陷都附一个独立复现脚本：`python3 repro/<脚本名>.py`。脚本在缺陷存在时以非 0
退出码结束并打印证据；缺陷被修复后应退出 0。

| # | 位置 | 问题 | 违反的保证 | 复现脚本 |
| --- | --- | --- | --- | --- |
| 1 | `Pool.acquire` | `wait()` 完全没传 `timeout` | 保证 2 | `repro/01_acquire_timeout_ignored.py` |
| 2 | `Pool.acquire` | 工厂抛异常后 `_live` 不回滚，名额泄漏 | 假满（与 `PoolExhausted` 语义不符） | `repro/02_factory_failure_leaks_slot.py` |
| 3 | `Pool.acquire` | 持锁调用 `factory()`，慢建连冻住整个池 | 保证 2 / 优雅退出 | `repro/03_factory_called_under_lock.py` |
| 4 | `Pool.close` | 不唤醒等待者（缺 `notify_all`） | 保证 3 | `repro/04_close_does_not_wake_waiters.py` |
| 5 | `Pool.acquire` / `Pool.release` | 关闭后等待者不复查 `_closed`，`release` 仍把连接放回池 | 保证 3 | `repro/05_closed_pool_still_hands_out_connection.py` |
| 6 | `Pool.close` | 关闭空闲连接后 `_live` 不减少（附带问题，非并发） | `live_count` 文档语义 | `repro/06_live_count_stale_after_close.py` |

---

## 缺陷 1：`acquire` 的等待完全忽略 `timeout`，会无限挂住

**位置**：`poolkit/pool.py` 的 `Pool.acquire`，池满分支：

```python
# 池满，等别人还回来
self._cond.wait()
if self._idle:
    return self._idle.pop()
raise PoolExhausted("等待连接超时")
```

**触发条件**：池满（`_live == _max_size` 且 `_idle` 为空）时调用
`acquire(timeout=任意值)`，并且没有一次 `release` 恰好唤醒它。这正是高峰期
「池满等待」的常态。

**后果**：

- `self._cond.wait()` 没有传任何超时，README 保证 2 说「最多等 `timeout` 秒就一定
  在这个时间附近返回（成功或抛异常）」，实际上调用会永远阻塞，即使传了 `timeout`。
  这就是高峰期「acquire 一直拿不到」的直接原因之一。
- 等待没有包在「条件循环」里：`wait()` 醒来后只要 `_idle` 为空就立刻抛
  `PoolExhausted`，不检查距 deadline 还剩多久。因此即使传了超时，也会因为
  虚假唤醒、或空闲连接被别的线程先拿走（一个新来的 `acquire` 走「池未满/有空闲」
  快路径抢先 pop）而在超时时间远没到的时候过早抛 `PoolExhausted`。

**正确做法**：用 deadline + 条件循环，把剩余时间传给条件变量：

```python
deadline = None if timeout is None else time.monotonic() + timeout
while not self._idle:
    if self._closed:
        raise PoolClosed(...)
    remaining = None if deadline is None else deadline - time.monotonic()
    if remaining is not None and remaining <= 0:
        raise PoolExhausted(...)
    self._cond.wait(remaining)
```

---

## 缺陷 2：工厂创建失败时名额不回滚，池子「假满」

**位置**：`Pool.acquire` 的新建分支：

```python
if self._live < self._max_size:
    self._live += 1
    return self._factory()
```

**触发条件**：`_live` 先加 1，随后 `self._factory()` 抛异常（真实项目里建库连接 /
建 socket 失败在高峰期很常见；`poolkit.fake.make_factory(failures=...)` 可模拟）。

**后果**：异常直接向外抛，但 `_live` 已经被加过且没有任何回滚。这个「名额」被永久
泄漏：池里一个真实连接都没有，`live_count` 却显示满了，此后所有 `acquire` 都进入
池满等待分支（叠加缺陷 1 就是永久挂住），形成线上看到的「明明没几个连接在用，
acquire 却一直拿不到」。按文档，`PoolExhausted` 只应在「池满且等不到」时出现，
池实际为空时不应进入这条路径。

**正确做法**：创建失败时回滚计数并唤醒其他等待者：

```python
if self._live < self._max_size:
    self._live += 1
    try:
        conn = self._factory()
    except BaseException:
        self._live -= 1
        self._cond.notify()
        raise
    return conn
```

---

## 缺陷 3：持着池锁调用 `factory()`，一个慢建连冻住整个池

**位置**：同 `Pool.acquire` 新建分支——`self._factory()` 是在
`with self._cond:` 持锁期间调用的。

**触发条件**：`factory` 创建连接耗时较长（真实场景建库连接几十到几百毫秒；
`make_factory(delay=...)` 可模拟），且建连期间有其他线程访问池子。

**后果**：工厂运行的整段时间内锁不释放，其他线程的 `release()`、`close()`、
`acquire()`（包括读 `live_count` / `idle_count`）全部阻塞在这把锁上。

- 高峰期一个慢建连会让正在还连接的线程也还不进去，空闲连接无法被其他等待者看到，
  表现为「连接池假满」；
- 优雅退出时 `close()` 要等这个慢建连结束才能拿到锁，退出时间被建连时长拖住，
  日志停在等连接 / 等关闭上；
- 同样使保证 2 失效：即使修好了 `wait(timeout)`，一个 `acquire(timeout=很小)`
  也可能因为等锁而被卡在超时之外。

**正确做法**：在锁外创建连接。锁内只做计数「占位」，释放锁后再调 `factory`，
建完再加锁校验池状态（此时可能已 `close()`），失败按缺陷 2 回滚。

---

## 缺陷 4：`close()` 不唤醒等待中的 `acquire`，保证 3 完全不成立

**位置**：`Pool.close`：

```python
with self._cond:
    self._closed = True
    for conn in self._idle:
        conn.close()
    self._idle.clear()
    # 没有 notify_all()
```

**触发条件**：有线程正阻塞在 `acquire` 的 `self._cond.wait()` 中（池满等待），
此时另一个线程调用 `close()`。

**后果**：`close()` 既没有 `notify()` 也没有 `notify_all()`，等待线程永远不会
醒来。README 保证 3 明确承诺「`close()` 之后，所有正在等待的 `acquire` 调用都会
得到 `PoolClosed`」，实际是这些线程永久挂住——这正是服务优雅退出时「偶尔卡住
不退出，日志停在等连接上」的直接原因。

**正确做法**：`close()` 在持锁末尾调用 `self._cond.notify_all()`；同时
`acquire` 醒来后必须先检查 `_closed`（见缺陷 5），否则只会被改成抛
`PoolExhausted`。

---

## 缺陷 5：等待路径醒来后不复查 `_closed`；关闭后 `release` 仍把连接放回池

**位置**：

- `Pool.acquire`：进入等待前检查过一次 `_closed`，但 `wait()` 返回后只看
  `_idle`，不再检查池是否已关闭；
- `Pool.release`：任何时候都把 `conn` 追加进 `_idle` 并 `notify()`，完全不检查
  `self._closed`。

**触发条件**：线程 A 在池满时等待；`close()` 之后，持有连接的线程 B 正常走
`try/finally` 把连接 `release` 回来（这是调用方文档示例鼓励的标准用法）。

**后果**（即使补上缺陷 4 的 `notify_all` 也仍然存在）：

1. B 的 `release` 把连接放进已关闭池的空闲列表并唤醒 A；A 醒来看到 `_idle`
   非空，直接把连接 pop 走——对一个已关闭的池，等待中的 `acquire` 拿到了连接
   而不是保证 3 承诺的 `PoolClosed`，调用方无法通过异常感知池已关闭。
2. 这个连接在 `close()` 执行时还被占用（文档承诺「正在被占用的不动」，这步
   本身没错），但它被还回来后池已经关闭、不会再有第二次 `close()` 清理它
   （`close` 可重复调用但只清理当时的 `_idle`），于是它永远不会被
   `conn.close()`，真实数据库 / socket 连接就此泄漏。

**正确做法**：

- `acquire` 等待循环中每次醒来先判 `self._closed`，关闭则抛 `PoolClosed`；
- `release` 在 `self._closed` 时不把连接放回池，而是直接对它执行
  `conn.close()`（并同步调整 `_live`，见缺陷 6），保证「占用中的连接在还回来时
  一定被关闭」。

---

## 缺陷 6（附带）：`close()` 关闭空闲连接后 `_live` 不减少

**位置**：`Pool.close` 清空 `_idle` 时没有相应减少 `_live`。

**触发条件 / 后果**：任意池在有空闲连接时 `close()`，之后读 `live_count`。
文档定义 `live_count` 为「已创建且未关闭的连接数（含正在被占用的）」，但被
`close()` 关掉的空闲连接仍被计入，例如 2 个空闲连接全部被关闭后
`live_count` 仍返回 2。这不是并发缺陷，也不违反三条保证的字面，但会让退出期的
监控/排障数据失真，且与缺陷 5 的计数修正应一起处理。

**正确做法**：关闭空闲连接时 `self._live -= len(self._idle)`；被占用连接在
`release` 时关闭（见缺陷 5）同样递减。
