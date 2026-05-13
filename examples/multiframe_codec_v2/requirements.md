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
- 4x4 and 8x8 zig-zag scan plus run-length coding.
- Exp-Golomb run/level coding.
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
- `expgolomb_enc`
- `block_packer`
- `deblock_filter`

For each generated block, include `python_source:
examples/multiframe_codec_v2/codec_golden.py`, a Verilog target under
`rtl/multiframe_codec_v2/`, and a cocotb testbench target under `tb/cocotb/`.

The software preflight on Mort at 128x72 showed the golden model can achieve:

- QP24: 2.8508 bpp, 47.98 dB, 667/1440 macroblocks selected as 8x8
- QP36: 1.1574 bpp, 39.48 dB, 635/1440 macroblocks selected as 8x8
- QP48: 0.3964 bpp, 32.00 dB, 925/1440 macroblocks selected as 8x8

Optimize for a clean, stream-oriented soft IP rather than MMIO. Do not require
memory map, register spec, or clock-tree elaboration unless explicitly enabled.
