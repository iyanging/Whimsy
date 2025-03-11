#  Copyright (c) 2025 iyanging
#
#  Whimsy is licensed under Mulan PSL v2.
#  You can use this software according to the terms and conditions of the Mulan PSL v2.
#  You may obtain a copy of Mulan PSL v2 at:
#      http://license.coscl.org.cn/MulanPSL2
#
#  THIS SOFTWARE IS PROVIDED ON AN "AS IS" BASIS, WITHOUT WARRANTIES OF ANY KIND,
#  EITHER EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO NON-INFRINGEMENT,
#  MERCHANTABILITY OR FIT FOR A PARTICULAR PURPOSE.
#
#  See the Mulan PSL v2 for more details.
#


from collections.abc import Callable, Generator
from functools import wraps
from types import GeneratorType
from typing import Any, cast


class Task[T]:
    """对连续运行程序片段的抽象, Continuation 的外在表现, Event Loop 调度的基本对象, 结果的容器.

    参考: https://en.wikipedia.org/wiki/Continuation
    """

    _parent: "Task[Any] | None"
    _generator: Generator["Task[Any] | None", Any, Any] | None
    _result: "T | _Missing"
    _exception: BaseException | None

    def __init__(self) -> None:
        self._parent = None
        self._generator = None
        self._result = _Missing()
        self._exception = None

    def x_set_generator(
        self,
        generator: Generator["Task[Any] | None", Any, Any],
    ) -> None:
        self._generator = generator

    def x_generator(self) -> Generator["Task[Any] | None", Any, Any]:
        if self._generator is None:
            raise Exception("generator should not be None")

        return self._generator

    def x_set_parent(self, parent: "Task[Any]") -> None:
        self._parent = parent

    def x_parent(self) -> "Task[Any] | None":
        return self._parent

    def set_result(self, result: T) -> None:
        if isinstance(self._result, _Missing) and self._exception is None:
            self._result = result

        else:
            raise InvalidStateError

    def set_exception(self, exception: BaseException) -> None:
        if isinstance(self._result, _Missing) and self._exception is None:
            self._exception = exception

        else:
            raise InvalidStateError

    def result(self) -> T:
        if not isinstance(self._result, _Missing):
            return self._result

        if self._exception is not None:
            raise self._exception

        raise InvalidStateError

    def done(self) -> bool:
        return (not isinstance(self._result, _Missing)) or self._exception is not None

    def __repr__(self) -> str:
        gen = cast("GeneratorType[Task[Any], Any, None] | None", self._generator)
        gen_qualname = gen.gi_code.co_qualname + "()" if gen is not None else None

        return f"<{self.__class__.__qualname__}, gen: {gen_qualname}, parent: {self._parent!r}>"


def awaitable[**P, R](
    #
    # 怎么理解 Generator[Task[Any] | None, Any, R] ?
    #
    #     对于1个模拟协程, 内部在使用 yield 时, 大概有两种目的:
    #
    #       1. Task[Any], Any, R -> 调用其他模拟协程:
    #           会把被调用的模拟协程创建的 Task[?] 给返出来,
    #           并且希望恢复执行时, 能在此断点处取得上该 Task[?] 里的结果
    #
    #       2. None, None, R -> 当前模拟协程等待操作完成, 主动归还执行流
    #
    func: Callable[P, Generator[Task[Any] | None, Any, R]],
) -> Callable[P, Task[R]]:
    """将 Generator 构造函数转换为 Task 构造函数.

    ...使得逻辑可以直接写在 Generator 构造函数中, 避免需要封装到 Task 里.
    """

    @wraps(func)
    def wrapper(*args: P.args, **kwargs: P.kwargs) -> Task[R]:
        task = get_running_loop().create_task()

        gen = func(*args, **kwargs)
        task.x_set_generator(gen)

        return task

    return wrapper


class EventLoop:
    """事件循环, 实现了对 Task 的调度."""

    _tasks_queue: list[Task[Any]]
    """Task 队列, 包含正在运行的, 以及待运行的 Task."""

    def __init__(self) -> None:
        self._tasks_queue = []

    def create_task(self) -> Task[Any]:
        return Task[Any]()

    def run_until_complete[T](self, task: Task[T]) -> T:
        self._tasks_queue.append(task)

        # loop 本体: 以某种判定条件, 取出下一个需要运行的 Task, 运行逻辑或返回结果
        while len(self._tasks_queue) > 0:
            # 某种判定条件: 直接取队列头部
            next_task = self._tasks_queue.pop(0)
            parent_task = next_task.x_parent()

            if not next_task.done():  # task 未完成, 继续向下执行
                running_task = next_task
                sending = None
                throwing = None

            elif parent_task is not None:  # task 已完成, 返回向上执行
                running_task = parent_task
                try:
                    sending = next_task.result()
                    throwing = None
                except BaseException as exc:
                    sending = None
                    throwing = exc

            else:
                continue

            # 已经抉择了待运行的 task, 删除临时变量避免后续代码误用
            del next_task
            del parent_task

            # 运行 generator
            try:
                running_gen = running_task.x_generator()

                if throwing is None:  # noqa: SIM108
                    new_task = running_gen.send(sending)

                else:
                    new_task = running_gen.throw(throwing)

            except StopIteration as stop:  # generator 运行完成, 取得结果
                result = stop.value
                running_task.set_result(result)

                # 将已经完成的 task 送回队列, 结果的返回由后续的循环来操作
                self._tasks_queue.append(running_task)

            except BaseException as exc:  # generator 发生了异常
                running_task.set_exception(exc)

                # 将已经完成的 task 送回队列, 异常的抛出由后续的循环来操作
                self._tasks_queue.append(running_task)

            else:  # generator 主动中断, 可能是自身依旧等待完成, 或触发了新的 task
                if new_task is not None:  # generator 触发了新的 task
                    new_task.x_set_parent(running_task)
                    # 控制流转交给新的 Task, 而无需将老的 Task 再次加入队列
                    # 待新的 Task 取得结果时, 从 parent 取得老的 Task
                    self._tasks_queue.append(new_task)

                else:
                    # generator 自身依旧等待完成, 重新加入队列
                    self._tasks_queue.append(running_task)

        # 当 Task 队列全部运行完毕后, 可以取得结果
        return task.result()


_running_loop = EventLoop()


def get_running_loop() -> EventLoop:
    return _running_loop


def run[T](task: Task[T]) -> T:
    return get_running_loop().run_until_complete(task)


class _Missing: ...


class InvalidStateError(Exception):
    """The operation is not allowed in this state."""
