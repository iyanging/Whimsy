from collections.abc import Callable, Generator
from functools import wraps
from time import time
from types import GeneratorType
from typing import Any, cast


class _Missing: ...


class Future[T]:
    """一次性的、用于传递结果数据的、可以设置回调的，容器."""

    _parent: "Future[Any] | None"
    _generator: Generator["Future[Any] | None", Any, Any] | None
    _result: T | _Missing

    def __init__(self) -> None:
        self._parent = None
        self._generator = None
        self._result = _Missing()

    def x_set_generator(
        self, generator: Generator["Future[Any] | None", Any, Any]
    ) -> None:
        self._generator = generator

    def x_generator(self) -> Generator["Future[Any] | None", Any, Any]:
        if self._generator is None:
            raise Exception("generator should not be None")

        return self._generator

    def x_set_parent(self, parent: "Future[Any]") -> None:
        self._parent = parent

    def x_parent(self) -> "Future[Any] | None":
        return self._parent

    def set_result(self, result: T) -> None:
        if not self.done():
            self._result = result
        else:
            raise Exception("Future already had result")

    def result(self) -> T:
        if self.done():
            return cast(T, self._result)
        else:
            raise Exception("Future has no result")

    def done(self) -> bool:
        return not isinstance(self._result, _Missing)

    def __repr__(self) -> str:
        gen = cast(GeneratorType[Future[Any], Any, None] | None, self._generator)
        gen_qualname = gen.gi_code.co_qualname + "()" if gen is not None else None

        return f"<{self.__class__.__qualname__}, gen: {gen_qualname}, parent: {repr(self._parent)}>"


def awaitable[**P, R](
    #
    # 怎么理解 Generator[Future[Any] | None, Any, R] ?
    #
    #     对于1个模拟协程, 内部在使用 yield 时, 大概有两种目的:
    #
    #       1. Future[Any], Any, R -> 调用其他模拟协程:
    #           会把被调用的模拟协程创建的 Future[?] 给返出来,
    #           并且希望恢复执行时, 能在此断点处取得上该 Future[?] 里的结果
    #
    #       2. None, None, R -> 当前模拟协程等待操作完成, 主动归还执行流
    #
    func: Callable[P, Generator[Future[Any] | None, Any, R]],
) -> Callable[P, Future[R]]:
    """将 Generator 构造函数转换为 Future 构造函数.

    ...使得逻辑可以直接写在 Generator 构造函数中, 避免需要封装到 Future 里.
    """

    @wraps(func)
    def wrapper(*args: P.args, **kwargs: P.kwargs) -> Future[R]:
        future = get_running_loop().create_future()

        gen = func(*args, **kwargs)
        future.x_set_generator(gen)

        return future

    return wrapper


class EventLoop:
    """事件循环, 实现了对 Future 的调度."""

    _futures_queue: list[Future[Any]]
    """Future 队列, 包含正在运行的, 以及待运行的 Future."""

    def __init__(self) -> None:
        self._futures_queue = []

    def create_future(self) -> Future[Any]:
        return Future[Any]()

    def run_until_complete[T](self, future: Future[T]) -> T:
        self._futures_queue.append(future)

        while len(self._futures_queue) > 0:
            next_future = self._futures_queue.pop(0)
            parent_future = next_future.x_parent()

            if not next_future.done():  # future 未完成, 继续向下执行
                running_future = next_future
                sending = None

            elif parent_future is not None:  # future 已完成, 返回向上执行
                running_future = parent_future
                sending = next_future.result()

            else:
                continue

            # 已经抉择了待运行的 future, 删除临时变量避免后续代码误用
            del next_future
            del parent_future

            # 运行 generator
            try:
                new_future = running_future.x_generator().send(sending)

            except StopIteration as stop:  # generator 运行完成, 取得结果
                result = stop.value
                running_future.set_result(result)

                # 将已经完成的 future 送回队列, 结果的返回由后续的循环来操作
                self._futures_queue.append(running_future)

            else:  # generator 主动中断, 可能是自身依旧等待完成, 或触发了新的 future
                if new_future is not None:  # generator 触发了新的 future
                    new_future.x_set_parent(running_future)
                    # 控制流转交给新的 Future, 若新的 Future 需要返回值则会创建 resume generator,
                    # 而无需将老的 Future 再次加入队列
                    self._futures_queue.append(new_future)

                else:
                    # generator 自身依旧等待完成
                    self._futures_queue.append(running_future)

        # 当 Future 队列全部运行完毕后, 可以取得结果
        return future.result()


_running_loop = EventLoop()


def get_running_loop() -> EventLoop:
    return _running_loop


def run[T](future: Future[T]) -> T:
    return get_running_loop().run_until_complete(future)


@awaitable
def gen_sleep(timeout: int) -> Generator[None, None, None]:
    start_time = time()

    while time() - start_time < timeout:
        yield

    return
