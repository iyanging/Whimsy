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

# ruff: noqa: B901, T201

from collections.abc import Generator
from time import time
from typing import Any

from gen_co import awaitable, run


@awaitable
def gen_sleep(timeout: int) -> Generator[None]:
    start_time = time()

    while time() - start_time < timeout:
        yield

    return


@awaitable
def hello(name: str) -> Generator[Any, None, str]:
    yield gen_sleep(2)
    return f"Hello, {name}"


@awaitable
def trigger() -> Generator[None]:
    yield
    raise Exception("TriggerError")


@awaitable
def main() -> Generator[Any, Any]:
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
