from collections.abc import Generator
from time import time
from typing import Any

from gen_co import awaitable, run


@awaitable
def gen_sleep(timeout: int) -> Generator[None, None, None]:
    start_time = time()

    while time() - start_time < timeout:
        yield

    return


@awaitable
def hello(name: str) -> Generator[Any, None, str]:
    yield gen_sleep(2)
    return f"Hello, {name}"


@awaitable
def trigger() -> Generator[None, None, None]:
    yield
    raise Exception("TriggerError")


@awaitable
def main() -> Generator[Any, Any, None]:
    try:
        yield trigger()
    except Exception as exc:
        print(f"Got Error: {exc}, ignored!")

    print("Current:", time())

    # 模拟协程最大的缺陷:
    # 类型推导不看 yield 后面的 hello() 调用, 而是 main() 的返回类型
    # Generator[Any, Any, None]
    #                ^^^
    result: str = yield hello("world")

    print("Current:", time(), result)


if __name__ == "__main__":
    run(main())
