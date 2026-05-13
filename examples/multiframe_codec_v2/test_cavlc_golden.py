import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(__file__))
import codec_golden as G


def _roundtrip_coeffs(block):
    bits = G.cavlc_encode_coefficients(block)
    decoded, offset = G.cavlc_decode_coefficients(bits, 0, shape=block.shape)
    assert offset == len(bits)
    np.testing.assert_array_equal(decoded, block)
    return bits


def test_cavlc_4x4_zeros_trailing_ones_roundtrip():
    block = np.array([
        [3, 0, 0, 0],
        [0, -2, 0, 0],
        [0, 0, 0, 1],
        [0, 0, -1, 1],
    ], dtype=np.int32)

    bits = _roundtrip_coeffs(block)

    scanned = G._scan_coefficients(block)
    nonzero = [v for v in scanned if v != 0]
    assert len(nonzero) == 5
    assert [abs(v) for v in reversed(nonzero)][0:3] == [1, 1, 1]
    assert bits


def test_cavlc_4x4_all_zero_block_roundtrip():
    block = np.zeros((4, 4), dtype=np.int32)
    bits = _roundtrip_coeffs(block)
    assert bits == G._ue_encode(0)


def test_cavlc_8x8_sparse_runs_roundtrip():
    block = np.zeros((8, 8), dtype=np.int32)
    block[0, 0] = 8
    block[0, 1] = -1
    block[2, 0] = 1
    block[7, 7] = -3
    block[6, 7] = 1

    bits = _roundtrip_coeffs(block)

    scanned = G._scan_coefficients(block)
    assert scanned.count(0) == 59
    assert bits


def test_encode_image_default_entropy_preserves_existing_stream():
    img = np.arange(16, dtype=np.uint8).reshape(4, 4) * 11
    default_bytes, default_blocks = G.encode_image(img, qp=24)
    explicit_bytes, explicit_blocks = G.encode_image(img, qp=24, entropy="expgolomb")
    assert default_bytes == explicit_bytes
    assert default_blocks == explicit_blocks


def test_encode_decode_image_cavlc_roundtrip_shape_and_quality():
    img = np.array([
        [12, 14, 18, 21, 40, 43, 45, 48],
        [15, 17, 19, 25, 44, 47, 49, 52],
        [18, 20, 23, 26, 47, 49, 53, 55],
        [20, 22, 25, 29, 51, 54, 56, 59],
        [80, 82, 85, 88, 120, 123, 126, 129],
        [84, 86, 89, 92, 124, 127, 130, 133],
        [88, 90, 94, 96, 128, 131, 134, 137],
        [92, 94, 97, 101, 132, 135, 138, 141],
    ], dtype=np.uint8)

    encoded, _blocks = G.encode_image(img, qp=24, entropy="cavlc")
    decoded = G.decode_image(encoded, *img.shape, qp=24, entropy="cavlc")

    assert decoded.shape == img.shape
    assert G.psnr(img, decoded) > 25.0


def test_encode_decode_v2_cavlc_8x8_path():
    img = np.tile(np.arange(16, dtype=np.uint8), (16, 1)) * 8
    encoded, meta, recon = G.encode_image_v2(img, qp=36, entropy="cavlc")
    decoded = G.decode_image_v2(encoded, *img.shape, qp=36, entropy="cavlc")

    assert meta
    assert {m["entropy"] for m in meta} == {"cavlc"}
    assert recon.shape == img.shape
    assert decoded.shape == img.shape
    assert G.psnr(img, decoded) > 25.0
