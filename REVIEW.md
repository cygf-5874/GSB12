# poolkit 并发审查报告

审查对象：`poolkit/pool.py`（版本 0.6.2）。判定标准：`README.md`「对外保证」三条
以及 README 公开 API 表中对 `live_count`、`acquire(timeout=...)` 的约定。
`poolkit/` 与 `tests/` 未做任何改动；每个缺陷配一个可独立运行的复现脚本，
缺陷存在时脚本退出码非 0，并打印可判定的证据。

复现脚本（在仓库根目录执行 `python3 repro/<file>.py`）：

| # | 函数 / 位置 | 违反的约定 | 复现脚本 |
| --- | --- | --- | --- |
| 1 | `Pool.acquire`，`poolkit/pool.py:92` | 保证 2 | `repro/acquire_ignores_timeout.py` |
| 2 | `Pool.acquire`，`poolkit/pool.py:89-90` | 保证 2、`live_count` 语义 | `repro/acquire_factory_failure_leaks_slot.py` |
| 3 | `Pool.close`，`poolkit/pool.py:107-111` | 保证 3 | `repro/close_does_not_wake_waiters.py` |
| 4 | `Pool.acquire`，`poolkit/pool.py:92-94` | 保证 3 | `repro/waiter_acquires_from_closed_pool.py` |

线上两类报障的对应关系：

- 「高峰期偶发连接池假满」：缺陷 2 是确定成因（建连偶发失败即永久占槽，槽位
  泄漏到池生命周期结束，且与缺陷 1 叠加后调用方连超时都等不到）。
- 「优雅退出卡住，日志停在等连接上」：缺陷 3 是确定成因（`close()` 不唤醒
  等待者，等待线程永久挂在 `Condition.wait`）；缺陷 4 是同一链路上的另一半
  问题（被唤醒后也不按保证抛 `PoolClosed`）。

---

## 缺陷 1：`acquire` 丢弃 `timeout` 参数，池满时无限等待

**位置**：`Pool.acquire`，`poolkit/pool.py:92`，`self._cond.wait()`。

**触发条件**：池中连接全部被占用、有线程以 `acquire(timeout=<正数>)` 等待，
且占用方迟迟不 `release`（或进程恰在此时准备退出）。不需要任何异常路径，
正常高峰流量即可触发。

**后果**：

- `self._cond.wait()` 是无超时的无条件等待。`timeout` 参数在整个函数里从未
  被使用，等待线程会一直挂起，直到别的线程 `notify` 它为止，与 timeout 取什么
  值无关。
- 直接违反保证 2（「最多等 timeout 秒，就一定在这个时间附近返回，成功或抛异常，
  不会无限期挂住」）；`PoolExhausted` 在这条路径上实际上永远不会抛出。
- 复现脚本实测：`acquire(timeout=0.3)` 在 0.9 秒后线程仍阻塞在
  `poolkit/pool.py:92`，直到 0.9 秒时另一线程 `release` 才返回「成功」，
  而不是约 0.3 秒时抛 `PoolExhausted`。

**正确做法**：把超时传给条件变量，并在循环里等待，醒来后按状态决定结果，
例如：

```python
deadline = None if timeout is None else time.monotonic() + timeout
with self._cond:
    while True:
        if self._closed:
            raise PoolClosed(...)
        if self._idle:
            return self._idle.pop()
        if self._live < self._max_size:
            try:
                self._live += 1
                return self._factory()
            finally:
                ...  # 见缺陷 2 的回滚
        remaining = None if deadline is None else deadline - time.monotonic()
        if remaining is not None and remaining <= 0:
            raise PoolExhausted(...)
        self._cond.wait(remaining)   # 必须带剩余超时
```

用 `while` 循环还能同时处理条件变量的伪唤醒，以及「多个等待者、一次 notify
只有一个能抢到连接」的情况（当前实现在这种情况下会把没抢到连接的线程直接
判成超时，见缺陷 4 的同一处代码）。

---

## 缺陷 2：工厂建连失败时槽位不回滚，连接池「假满」

**位置**：`Pool.acquire`，`poolkit/pool.py:89-90`：

```python
self._live += 1
return self._factory()
```

**触发条件**：等待池有空位走到「新建连接」分支时，`factory()` 抛异常
（高峰期数据库/socket 建连超时、被限流等）。调用方收到工厂异常是正常的，
但 `_live` 已经在抛异常前加了 1，且没有任何回滚。

**后果**：

- 一次工厂失败就永久泄漏一个槽位：`live_count` 比池里真实存在的连接多 1。
  README 约定 `live_count` 是「已创建且未关闭的连接数（含正在被占用的）」，
  失败的工厂根本没创建出连接，这个计数从此失真。
- 泄漏槽位到 `max_size` 后（`max_size` 较小或失败几次后必然发生），池里实际
  空闲、却没有任何连接在用，新的 `acquire` 却一律走「池满」分支去等待——
  正是线上的「连接池假满」。与缺陷 1 叠加后，这次等待连超时都不会有，调用方
  会永久挂住。
- 状态没有自愈路径：正常的 `release` 无法归还一个从未创建成功的连接，
  `close()` 也不会纠正 `_live`，只能废弃整个池。
- 复现脚本（`max_size=1`，工厂第一次调用失败）实测：异常透传后
  `live_count=1, idle_count=0`，调用方手里也没有连接；随后的
  `acquire(timeout=0.3)` 在 0.9 秒后仍阻塞在 `poolkit/pool.py:92`。

**正确做法**：建连失败时回滚计数，把「计数 +1」与「连接真的造出来」绑定。
工厂调用建议移到锁外（避免慢建连带锁，不影响正确性），锁内只保留计数提交，
例如：

```python
# 锁内
if self._live >= self._max_size:
    ... 等待 ...
self._live += 1
# 锁外造连接
try:
    conn = self._factory()
except BaseException:
    with self._cond:
        self._live -= 1
        self._cond.notify()   # 让一个等待者重试新建
    raise
```

---

## 缺陷 3：`close()` 不唤醒等待中的 `acquire`，优雅退出永久卡住

**位置**：`Pool.close`，`poolkit/pool.py:105-111`。

**触发条件**：池满，有线程阻塞在 `acquire` 的 `self._cond.wait()`
（`poolkit/pool.py:92`）；此时另一个线程调用 `close()`。这正是服务优雅
退出时的典型时序：业务线程在等连接，关闭流程开始关池。

**后果**：

- `close()` 只做了 `self._closed = True`、关闭并清空空闲列表，**没有
  `notify_all()`**。而 `Condition.wait()` 不会因为外部状态变化自己醒来，
  等待线程就永远挂在 `poolkit/pool.py:92`。
- 直接违反保证 3（「`close()` 之后，所有正在等待的 `acquire` 调用都会得到
  `PoolClosed`」）。在线程未被设为 daemon 的服务里，表现为进程退出时挂住，
  日志最后一行停在等待连接上——与线上第二类报障完全一致。
- 复现脚本实测：`close()` 返回后 0.8 秒，等待线程仍存活，调用栈卡在
  `threading.Condition.wait` ← `poolkit/pool.py:92`。

**正确做法**：`close()` 在持锁置位 `_closed` 后唤醒全部等待者：

```python
def close(self):
    with self._cond:
        if self._closed:
            return
        self._closed = True
        for conn in self._idle:
            conn.close()
        self._idle.clear()
        self._cond.notify_all()
```

只有 `notify_all()` 还不够，等待者醒来后必须复查 `_closed`，否则就是缺陷 4。

---

## 缺陷 4：等待中的 `acquire` 被唤醒后不复查关闭状态，会从已关闭的池取到连接

**位置**：`Pool.acquire`，`poolkit/pool.py:92-94`：

```python
self._cond.wait()
if self._idle:
    return self._idle.pop()
```

**触发条件**：线程在池满时等待；`close()` 发生在它等待期间；随后占用方
`release(conn)`（`release` 不检查池是否已关闭），等待者被 `notify` 唤醒。
即使按缺陷 3 给 `close()` 补了 `notify_all`，只要醒来后不复查，本缺陷依旧。

**后果**：

- 醒来后的代码只检查 `_idle`，不检查 `_closed`，于是直接 `pop()` 返回一个
  连接，而不是保证 3 承诺的 `PoolClosed`。
- 更糟的是：当 `release` 发生在 `close()` 之后时，`close()` 当时关不到这个
  尚被占用的连接；`release` 把它放进 `_idle` 但不会关它，随后它就被等待者
  取走。调用方在池已关闭后拿到一个 `closed == False` 的连接继续使用，关闭
  顺序与资源回收语义都被破坏。
- 复现脚本实测：`close()` 完成（`pool.closed=True`）之后，等待的 `acquire`
  返回的是 `<FakeConnection conn-1>`（而非 `PoolClosed`），且该连接
  `conn.closed == False`。

**正确做法**：等待必须放在条件循环里，每次醒来先复查关闭状态（与缺陷 1 的
修法是同一处）：

```python
while not self._closed:
    if self._idle:
        return self._idle.pop()
    if self._live < self._max_size:
        ...
    self._cond.wait(remaining)
raise PoolClosed("连接池已关闭")
```

另外建议 `release()` 在池已关闭时不要把连接放回 `_idle`，而是直接
`conn.close()` 并维持计数，避免关闭后归还的连接漏关并继续被分发。

---

## 看过但不作为结论上报的点

以下现象经过验证，要么不与 README 的三条保证冲突、要么无法在不改被测代码的
前提下确定复现，按「不把猜测当结论」的要求只做记录，不计入上表：

- `release(conn)` 不校验 `conn` 是否为本池发出的连接，且「重复 release 幂等」
  仅用 `conn in self._idle` 判重，依赖连接对象的 `__eq__`。这属于接口设计层面
  的取舍，README 也只承诺同一连接重复 release 幂等，未发现与三条保证直接冲突
  的并发路径，故不报。
- `self._factory()` 在持锁状态下调用（`poolkit/pool.py:90`），慢工厂会放大
  锁竞争、让其他 acquire/release/close 排队，但这是性能问题，不改变上述任何
  正确性结论。
- 条件变量伪唤醒（spurious wakeup）在 CPython 当前实现下不会凭空发生，无法
  构造不修改 `poolkit/` 的确定性复现，因此不单列；缺陷 1 给出的 `while` 循环
  修法已一并覆盖该情况。
