"""Python golden model for expgolomb_enc — bit-exact to PyH264.VLC.expgolomb_enc.

Interface mirrors the Verilog block's per-coefficient encode call so the
cocotb testbench can compare DUT output against this on every beat.
"""
from __future__ import annotations


def _encode_one(value: int) -> tuple[int, int]:
    """Return (codeword_msb_aligned, length_bits) for one int16 coefficient.

    codeword bit (31) is the first transmitted bit; bits below
    (32 - length_bits) are zero. length_bits is 1..32 (0 → length=1, code='1').
    Returns (0, 0) for any value whose codeword would exceed 32 bits.
    """
    if value == 0:
        codenum = 0
    elif value > 0:
        codenum = 2 * value - 1
    else:
        codenum = -2 * value

    coded = codenum + 1  # bin(coded) starts with the leading '1'
    nbits = coded.bit_length()
    length = 2 * nbits - 1  # (nbits - 1) leading zeros + nbits-bit binary
    if length > 32:
        return (0, 0)
    codeword_msb = coded << (32 - length)
    return (codeword_msb & 0xFFFFFFFF, length)


def encode(value: int) -> tuple[int, int]:
    """Public name for the testbench."""
    return _encode_one(int(value))


def expected_bitstring(value: int) -> str:
    """Return the codeword as a '0'/'1' string (used to cross-check PyH264 LUT)."""
    cw, length = _encode_one(value)
    if length == 0:
        return ""
    return format(cw >> (32 - length), f"0{length}b")


# Sanity self-test (matches the table in blocks.yaml)
_SELF_TEST = {
    0: "1", 1: "010", -1: "011",
    2: "00100", -2: "00101",
    3: "00110", -3: "00111",
    4: "0001000", -4: "0001001",
}
if __name__ == "__main__":
    for v, want in _SELF_TEST.items():
        got = expected_bitstring(v)
        assert got == want, f"value={v} got={got} want={want}"
    print("OK — golden matches PyH264 reference table")
