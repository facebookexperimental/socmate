# H.264-Inspired Soft-IP Codec V2 Requirements

Build a Sky130 Verilog-2005 soft IP block for a grayscale H.264-inspired
I-frame codec, grounded in the Python golden model at:

`examples/multiframe_codec_v2/codec_golden.py`

The architecture must start from this golden model and decompose it into RTL
blocks suitable for SocMate generation. This is not required to be a compliant
H.264 bitstream, but it should preserve the H.264-inspired coding structure.

Required codec features:

- I-frame coding path.
- Intra prediction directions: DC, vertical, and horizontal.
- 8x8 macroblocks.
- Per-macroblock selection between one 8x8 transform block and four 4x4
  transform subblocks.
- Mode decision using a simple rate-distortion proxy consistent with
  `encode_image_v2()`.
- 4x4 and 8x8 DCT-II transform support.
- Scalar QP quantization/dequantization.
- 4x4 and 8x8 zig-zag scan.
- H.264-style CAVLC coefficient coding for TotalCoeff, TrailingOnes,
  TotalZeros, and RunBefore. Keep Exp-Golomb only as an explicitly selectable
  software fallback, not as the hardware entropy target.
- Byte-oriented bitstream packing.
- Simplified H.264-style deblocking filter on every 4-pixel boundary,
  including internal 4x4 boundaries inside selected 8x8 macroblocks.

The block diagram should keep the design tractable for SocMate by using these
blocks unless the architecture stage finds a clearly better decomposition:

- `intra_predict`
- `transform_select`
- `quantize_select`
- `mode_decision`
- `zigzag_rle_select`
- `cavlc_enc`
- `block_packer`
- `deblock_filter`

For each generated block, include `python_source:
examples/multiframe_codec_v2/codec_golden.py`, a Verilog target under
`rtl/multiframe_codec_v2/`, and a cocotb testbench target under `tb/cocotb/`.

The software preflight on the 10-frame Mort GIF crop at 640x360 showed the
CAVLC golden model can achieve:

- QP24: 2.8674 bpp, 49.16 dB, 11330/36000 macroblocks selected as 8x8
- QP36: 1.0233 bpp, 38.76 dB, 13143/36000 macroblocks selected as 8x8
- QP48: 0.2161 bpp, 34.04 dB, 25568/36000 macroblocks selected as 8x8

Optimize for a clean, stream-oriented soft IP rather than MMIO. Do not require
memory map, register spec, or clock-tree elaboration unless explicitly enabled.

Frame-control lifecycle invariant:

- The design must have an explicit frame-start/input-active lifecycle event.
  A control block must not infer a new frame solely from an idle output register,
  ready/valid availability, or the ability to emit a status token.
- `codec_busy` must be tied to real occupancy: accepted input/frame activity,
  live pipeline state, non-empty/draining output FIFO state, valid status inputs,
  and unaccepted held output transactions. After a frame-end flush is accepted and
  the output FIFO reports empty/drained with frame TLAST seen, `codec_busy` must
  be able to deassert and remain low until the next explicit frame-start event.
- Block, smoke, integration, and validation DV must include a VCD/FST waveform
  check for this lifecycle: frame start, frame end flush, FIFO drain, idle
  deassertion, and no unintended immediate restart.
