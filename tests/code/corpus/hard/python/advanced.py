import asyncio
import functools


def outer(value):
    def inner(other):
        return value + other
    return inner


async def fetch(url):
    await asyncio.sleep(0)
    return url


@functools.lru_cache(maxsize=8)
def cached(value):
    return value


class Meta(type):
    def __call__(cls, *args):
        return super().__call__(*args)


class Container(metaclass=Meta):
    class Inner:
        def ping(self):
            return 1

    @property
    def size(self):
        return 0

    @staticmethod
    def build():
        return Container()

    async def load(self):
        return await fetch("x")
