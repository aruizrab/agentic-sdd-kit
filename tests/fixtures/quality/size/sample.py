"""Module docstring
spanning lines."""

import os


def outer(a,
          b):
    """Docstring of outer.

    More text.
    """
    x = 1

    def inner():
        return """
text that is dedented
"""

    # comment
    return x


class C:
    """Class docstring."""

    def method(self) -> int:
        return 1

    async def amethod(self):
        pass


def black_formatted(
    first: int,
    second: str,
) -> dict:
    data = {
        "k": 1,
    }
    return data


def one_liner(): return 1
