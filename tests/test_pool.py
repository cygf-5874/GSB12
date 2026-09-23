import unittest

from poolkit import Pool, PoolClosed, PoolError, PoolExhausted
from poolkit.fake import FakeConnection, make_factory


class ConstructionTest(unittest.TestCase):
    def test_max_size_must_be_positive(self):
        with self.assertRaises(ValueError):
            Pool(make_factory(), 0)
        with self.assertRaises(ValueError):
            Pool(make_factory(), -1)

    def test_factory_must_be_callable(self):
        with self.assertRaises(TypeError):
            Pool(None)

    def test_initial_state(self):
        pool = Pool(make_factory(), 2)
        self.assertEqual(pool.live_count, 0)
        self.assertEqual(pool.idle_count, 0)
        self.assertEqual(pool.max_size, 2)
        self.assertFalse(pool.closed)

    def test_exception_hierarchy(self):
        self.assertTrue(issubclass(PoolExhausted, PoolError))
        self.assertTrue(issubclass(PoolClosed, PoolError))


class AcquireTest(unittest.TestCase):
    def test_acquire_when_empty_creates_connection(self):
        pool = Pool(make_factory(), 2)
        conn = pool.acquire()
        self.assertIsInstance(conn, FakeConnection)
        self.assertEqual(pool.live_count, 1)
        self.assertEqual(pool.idle_count, 0)

    def test_acquire_up_to_max_size(self):
        pool = Pool(make_factory(), 2)
        first = pool.acquire()
        second = pool.acquire()
        self.assertIsNot(first, second)
        self.assertEqual(pool.live_count, 2)

    def test_released_connection_is_reused(self):
        pool = Pool(make_factory(), 1)
        conn = pool.acquire()
        pool.release(conn)
        self.assertEqual(pool.idle_count, 1)
        again = pool.acquire()
        self.assertIs(again, conn)
        self.assertEqual(pool.live_count, 1)

    def test_acquire_after_close_raises(self):
        pool = Pool(make_factory(), 2)
        pool.close()
        with self.assertRaises(PoolClosed):
            pool.acquire()

    def test_acquire_returns_whatever_the_factory_returns(self):
        marker = object()
        pool = Pool(lambda: marker, 1)
        self.assertIs(pool.acquire(), marker)


class ReleaseTest(unittest.TestCase):
    def test_release_is_idempotent(self):
        pool = Pool(make_factory(), 2)
        conn = pool.acquire()
        pool.release(conn)
        pool.release(conn)
        self.assertEqual(pool.idle_count, 1)

    def test_release_keeps_live_count(self):
        pool = Pool(make_factory(), 2)
        conn = pool.acquire()
        pool.release(conn)
        self.assertEqual(pool.live_count, 1)

    def test_release_makes_connection_available_again(self):
        pool = Pool(make_factory(), 1)
        conn = pool.acquire()
        pool.release(conn)
        self.assertIs(pool.acquire(), conn)


class CloseTest(unittest.TestCase):
    def test_close_closes_idle_connections(self):
        pool = Pool(make_factory(), 2)
        conn = pool.acquire()
        pool.release(conn)
        pool.close()
        self.assertTrue(conn.closed)
        self.assertEqual(pool.idle_count, 0)
        self.assertTrue(pool.closed)

    def test_close_is_idempotent(self):
        pool = Pool(make_factory(), 2)
        pool.close()
        pool.close()
        self.assertTrue(pool.closed)

    def test_close_does_not_touch_busy_connections(self):
        pool = Pool(make_factory(), 2)
        conn = pool.acquire()
        pool.close()
        self.assertFalse(conn.closed)


if __name__ == "__main__":
    unittest.main()
