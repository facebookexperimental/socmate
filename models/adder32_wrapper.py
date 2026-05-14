"""Golden wrapper behavior for the adder32 backend smoke harness."""

from __future__ import annotations

from models.adder32 import add32


class Adder32WrapperModel:
    """Tiny register/readback model around the raw adder32 reference."""

    def __init__(self) -> None:
        self.a = 0
        self.b = 0
        self.cin = 0
        self.sum = 0
        self.cout = 0
        self.result_valid = 0

    def reset(self) -> None:
        self.__init__()

    def write(self, addr: int, data: int) -> None:
        addr = int(addr) & 0xF
        data = int(data) & 0xFFFFFFFF
        if addr == 0:
            self.a = data
        elif addr == 1:
            self.b = data
        elif addr == 2:
            self.cin = data & 1
        elif addr == 3 and (data & 1):
            self.sum, self.cout = add32(self.a, self.b, self.cin)
            self.result_valid = 1

    def read(self, addr: int) -> int:
        addr = int(addr) & 0xF
        if addr == 0:
            return self.a
        if addr == 1:
            return self.b
        if addr == 2:
            return self.cin
        if addr == 3:
            return self.sum
        if addr == 4:
            return self.cout
        if addr == 5:
            return self.result_valid
        return 0
