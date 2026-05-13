import os

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import ClockCycles, RisingEdge


CLOCK_PERIOD_NS = 20
MAX_CYCLES = int(os.environ.get("MAX_CYCLES", "20000"))
QP_SEL = int(os.environ.get("QP_SEL", "0")) & 0x3


async def reset_dut(dut):
    dut.s_axis_pixel_tdata.value = 0
    dut.s_axis_pixel_tvalid.value = 0
    dut.s_axis_pixel_tlast.value = 0
    dut.s_axis_pixel_tuser.value = 0
    dut.s_axis_status_tdata.value = 0
    dut.s_axis_status_tvalid.value = 0
    dut.s_axis_status_tlast.value = 0
    dut.m_axis_byte_tready.value = 1
    dut.scan_en.value = 1
    dut.debug_mode.value = 0
    dut.rst_n.value = 0
    await ClockCycles(dut.clk, 8)
    dut.rst_n.value = 1
    await ClockCycles(dut.clk, 4)


async def send_first_macroblock(dut, value=128):
    accepted = 0
    for idx in range(64):
        dut.s_axis_pixel_tdata.value = value
        dut.s_axis_pixel_tvalid.value = 1
        dut.s_axis_pixel_tlast.value = 0
        dut.s_axis_pixel_tuser.value = ((QP_SEL & 0x3) << 1) | (1 if idx == 0 else 0)
        for _ in range(MAX_CYCLES):
            await RisingEdge(dut.clk)
            if int(dut.s_axis_pixel_tready.value):
                accepted += 1
                break
        else:
            raise AssertionError(f"timed out waiting for pixel ready at beat {idx}")

    dut.s_axis_pixel_tvalid.value = 0
    dut.s_axis_pixel_tlast.value = 0
    dut.s_axis_pixel_tuser.value = 0
    return accepted


async def collect_some_bytes(dut, min_bytes=1):
    data = []
    for _ in range(MAX_CYCLES):
        await RisingEdge(dut.clk)
        if int(dut.m_axis_byte_tvalid.value) and int(dut.m_axis_byte_tready.value):
            data.append(int(dut.m_axis_byte_tdata.value) & 0xFF)
            if len(data) >= min_bytes:
                return data
    raise AssertionError("timed out waiting for output byte from v2 chip_top")


@cocotb.test()
async def test_first_macroblock_emits_bytes(dut):
    cocotb.start_soon(Clock(dut.clk, CLOCK_PERIOD_NS, units="ns").start())
    await reset_dut(dut)

    rx_task = cocotb.start_soon(collect_some_bytes(dut, min_bytes=1))
    accepted = await send_first_macroblock(dut, value=128)
    data = await rx_task

    assert accepted == 64
    assert len(data) >= 1
    assert int(dut.error_flag.value) == 0
    assert int(dut.cavlc_protocol_assert.value) == 0
    assert int(dut.cavlc_code_overflow_assert.value) == 0
    assert int(dut.packer_overflow_assert.value) == 0
