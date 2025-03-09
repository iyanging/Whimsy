from collections.abc import Generator
from time import time
from typing import Any

from gen_co import awaitable, gen_sleep, run


@awaitable
def hello(name: str) -> Generator[Any, None, str]:
    yield gen_sleep(2)
    return f"Hello, {name}"


@awaitable
def main() -> Generator[Any, Any, None]:
    print("Current:", time())

    # 模拟协程最大的缺陷:
    # 类型推导不看 yield 后面的 hello() 调用, 而是 main() 的返回类型
    result: str = yield hello("world")

    print("Current:", time(), result)


if __name__ == "__main__":
    run(main())
