"""Software golden encoder + decoder matching the hardware semantics.

Pipeline (encoder):
    pixels (uint8)
      -> frame_subtract: subtract 128 for I-frames, else subtract prev
      -> 4x4 fp16 DCT-II (separable, orthonormal)
      -> quantize_fp: round(coef / step) where step = 2^((qp-12)/6)
      -> zigzag scan + run-length encode -> (run, level) pairs + EOB sentinel
      -> Exp-Golomb encode each run + level
      -> bitstream: 1-bit frame_type prefix + concatenated codewords, byte-aligned

Decoder reverses everything.

This file mirrors the RTL bit-exact for the back-half (expgolomb + packer); the
DCT and quantize stages match np.round (banker's rounding) up to fp16 rounding
in the reciprocal-step ROM.
"""
import math
import numpy as np

# ---------------------------------------------------------------------------
# Zig-zag scan order for 4x4 (column-major variant matching the spec)
# Indices into a row-major 4x4 flattened to length 16.
ZIGZAG_4x4 = [
    0,  1,  4,  8,
    5,  2,  3,  6,
    9, 12, 13, 10,
    7, 11, 14, 15,
]

# ---------------------------------------------------------------------------
# 4x4 fp16 DCT matrix (separable, orthonormal). row k of D is the kth basis.
def dct4_matrix():
    D = np.zeros((4, 4), dtype=np.float64)
    for k in range(4):
        ak = math.sqrt(1.0 / 4.0) if k == 0 else math.sqrt(2.0 / 4.0)
        for j in range(4):
            D[k, j] = ak * math.cos(math.pi * (2 * j + 1) * k / 8.0)
    return D.astype(np.float16)


DCT4 = dct4_matrix()
IDCT4 = DCT4.T          # orthonormal -> transpose is inverse


def dct_4x4(block):
    """block: 4x4 int16 residual.  Returns 4x4 fp16 coefficients."""
    b = block.astype(np.float16)
    return (DCT4 @ b @ DCT4.T).astype(np.float16)


def idct_4x4(coef):
    """coef: 4x4 fp16 quantized levels (dequantized).  Returns 4x4 int16 residual."""
    c = coef.astype(np.float16)
    return np.round((IDCT4 @ c @ IDCT4.T).astype(np.float64)).astype(np.int32)


# ---------------------------------------------------------------------------
# Quantize
def step_for_qp(qp):
    return 2.0 ** ((qp - 12) / 6.0)


# H.264-style 4x4 intra quantization weights (normalized to mean 16 so a flat
# matrix recovers the original behavior).  Lower step for DC and low-frequency
# coefficients, higher step for high-freq.  Values cribbed from the JPEG
# luminance table's upper-left 4x4 then scaled.
QUANT_MATRIX_4x4 = np.array([
    [13, 12, 14, 18],
    [12, 13, 16, 22],
    [14, 16, 20, 26],
    [18, 22, 26, 32],
], dtype=np.float64)
QUANT_MATRIX_4x4 *= 16.0 / QUANT_MATRIX_4x4.mean()      # renormalize to mean 16


def quantize(coef, qp, use_matrix=False):
    s = step_for_qp(qp)
    if use_matrix:
        eff = s * QUANT_MATRIX_4x4 / 16.0
    else:
        eff = s
    return np.round(coef.astype(np.float64) / eff).astype(np.int32)


def dequantize(level, qp, use_matrix=False):
    s = step_for_qp(qp)
    if use_matrix:
        eff = s * QUANT_MATRIX_4x4 / 16.0
    else:
        eff = s
    return (level.astype(np.float64) * eff).astype(np.float16)


# ---------------------------------------------------------------------------
# Zig-zag + run-length
def zigzag_rle(quant_4x4):
    """Returns list of (run, level) pairs ending with (0,0) EOB sentinel."""
    flat = quant_4x4.flatten()
    scanned = [int(flat[i]) for i in ZIGZAG_4x4]
    pairs = []
    run = 0
    for v in scanned:
        if v == 0:
            run += 1
        else:
            pairs.append((run, v))
            run = 0
    pairs.append((0, 0))     # EOB sentinel; trailing zeros are absorbed
    return pairs


def unzigzag_rle(pairs):
    """Inverse: pairs ending in (0,0) sentinel -> 4x4 quantized levels."""
    scanned = []
    for run, level in pairs[:-1]:
        scanned.extend([0] * run)
        scanned.append(level)
    while len(scanned) < 16:
        scanned.append(0)
    # invert zigzag
    inv = [0] * 16
    for i, dst in enumerate(ZIGZAG_4x4):
        inv[dst] = scanned[i]
    return np.array(inv, dtype=np.int32).reshape(4, 4)


# ---------------------------------------------------------------------------
# Exp-Golomb signed encode/decode (PyH264 semantics)
def expgolomb_encode(v):
    if v == 0:
        codenum = 0
    elif v > 0:
        codenum = 2 * v - 1
    else:
        codenum = -2 * v
    val = codenum + 1
    bit_len = val.bit_length()
    L = 2 * bit_len - 1
    return format(val, f'0{L}b')


def expgolomb_decode_stream(bits, offset):
    """Returns (signed value, new offset).  bits is a string of '0'/'1'."""
    # count leading zeros
    n = 0
    while offset + n < len(bits) and bits[offset + n] == '0':
        n += 1
    L = 2 * n + 1
    if offset + L > len(bits):
        return None, offset
    val = int(bits[offset:offset + L], 2)
    codenum = val - 1
    if codenum == 0:
        signed = 0
    elif codenum % 2 == 1:
        signed = (codenum + 1) // 2
    else:
        signed = -(codenum // 2)
    return signed, offset + L


# ---------------------------------------------------------------------------
# Bitstream packing per block: 1-bit frame_type + codeword stream, byte aligned
def pack_block(pairs, frame_is_intra):
    bits = ['1' if frame_is_intra else '0']
    for run, level in pairs:
        bits.append(expgolomb_encode(run))
        bits.append(expgolomb_encode(level))
    return ''.join(bits)


# ---------------------------------------------------------------------------
# Intra prediction (PyH264-style, 3 modes per 4x4 block)
# Modes:  0 = DC (mean of top + left)
#         1 = Vertical    (copy top row down)
#         2 = Horizontal  (copy left column right)
# Top-edge blocks: V unavailable -> fall back to DC.
# Left-edge blocks: H unavailable -> fall back to DC.
# Top-left block: only DC with default neighbors (=128 like before).
def _predictor(mode, top, left):
    """Build a 4x4 predictor.  top/left are 4-element int16 arrays or None."""
    pred = np.zeros((4, 4), dtype=np.int16)
    if mode == 1:                                          # Vertical
        assert top is not None
        pred[:] = top[None, :]
    elif mode == 2:                                        # Horizontal
        assert left is not None
        pred[:] = left[:, None]
    else:                                                  # DC
        if top is not None and left is not None:
            dc = (int(top.sum()) + int(left.sum()) + 4) // 8
        elif top is not None:
            dc = (int(top.sum()) + 2) // 4
        elif left is not None:
            dc = (int(left.sum()) + 2) // 4
        else:
            dc = 128
        pred[:] = dc
    return pred


def _available_modes(by, bx):
    """Return list of modes available for the 4x4 block at (by, bx) in pixels."""
    has_top  = (by > 0)
    has_left = (bx > 0)
    modes = [0]                                            # DC always works
    if has_top:  modes.append(1)
    if has_left: modes.append(2)
    return modes


def _get_neighbors(recon, by, bx):
    top  = recon[by - 1, bx:bx + 4].astype(np.int16) if by > 0 else None
    left = recon[by:by + 4, bx - 1].astype(np.int16) if bx > 0 else None
    return top, left


def encode_image(pixels, qp=36, use_intra_pred=False):
    """pixels: H x W uint8.  Returns bytes + per-block metadata for debug."""
    H, W = pixels.shape
    assert H % 4 == 0 and W % 4 == 0, 'must be multiple of 4'
    bits = []
    block_bits_list = []

    # Reconstructed pixels (only needed when intra prediction is on).
    recon = np.zeros((H, W), dtype=np.int16)

    for by in range(0, H, 4):
        for bx in range(0, W, 4):
            blk = pixels[by:by + 4, bx:bx + 4].astype(np.int16)

            if not use_intra_pred:
                # Original path: subtract 128.
                resid = blk - 128
                coef  = dct_4x4(resid)
                level = quantize(coef, qp)
                pairs = zigzag_rle(level)
                bblk  = pack_block(pairs, frame_is_intra=True)
                bits.append(bblk)
                block_bits_list.append(bblk)
                continue

            # Intra-pred path: try each available mode, pick smallest SAD
            # residual energy (cheap proxy for true RD cost).
            top, left = _get_neighbors(recon, by, bx)
            modes = _available_modes(by, bx)
            best_mode = 0
            best_resid = None
            best_pred  = None
            best_sad   = None
            for m in modes:
                pred = _predictor(m, top, left)
                resid = blk - pred
                sad = int(np.abs(resid).sum())
                if best_sad is None or sad < best_sad:
                    best_sad = sad
                    best_mode = m
                    best_resid = resid
                    best_pred  = pred

            # Encode residual through DCT->quant->zigzag-rle->Exp-Golomb
            coef = dct_4x4(best_resid)
            level = quantize(coef, qp)
            pairs = zigzag_rle(level)

            # Bitstream: 1-bit frame_is_intra + 2-bit mode + codewords
            bblk = '1'                                     # frame_is_intra = 1
            bblk += format(best_mode, '02b')               # 2-bit mode
            for run, lvl in pairs:
                bblk += expgolomb_encode(run)
                bblk += expgolomb_encode(lvl)
            bits.append(bblk)
            block_bits_list.append(bblk)

            # Reconstruct this block to update recon[] for future neighbors.
            deq = dequantize(level, qp)
            resid_recon = idct_4x4(deq)
            block_recon = best_pred + resid_recon
            recon[by:by + 4, bx:bx + 4] = np.clip(block_recon, 0, 255)

    s = ''.join(bits)
    while len(s) % 8 != 0:
        s += '0'
    by = bytearray(int(s[i:i+8], 2) for i in range(0, len(s), 8))
    return bytes(by), block_bits_list


def decode_image(byte_stream, H, W, qp=36, do_deblock=False, use_intra_pred=False):
    """Reverse encode_image; expects num_blocks == H*W/16."""
    bits = ''.join(format(b, '08b') for b in byte_stream)
    offset = 0
    out = np.zeros((H, W), dtype=np.uint8)
    n_blocks_y = H // 4
    n_blocks_x = W // 4
    for by_idx in range(n_blocks_y):
        for bx_idx in range(n_blocks_x):
            # 1-bit frame_type
            _frame_is_intra = (bits[offset] == '1')
            offset += 1

            if use_intra_pred:
                mode = int(bits[offset:offset + 2], 2)
                offset += 2
            else:
                mode = None

            pairs = []
            while True:
                run, offset = expgolomb_decode_stream(bits, offset)
                if run is None:
                    raise ValueError('truncated stream while decoding run')
                level, offset = expgolomb_decode_stream(bits, offset)
                if level is None:
                    raise ValueError('truncated stream while decoding level')
                pairs.append((run, level))
                if run == 0 and level == 0:
                    break

            quant = unzigzag_rle(pairs)
            coef  = dequantize(quant, qp)
            resid = idct_4x4(coef)

            by = by_idx * 4; bx = bx_idx * 4
            if use_intra_pred:
                top, left = _get_neighbors(out, by, bx)
                pred = _predictor(mode, top, left)
                pixels = np.clip(pred + resid, 0, 255).astype(np.uint8)
            else:
                pixels = np.clip(resid + 128, 0, 255).astype(np.uint8)
            out[by:by + 4, bx:bx + 4] = pixels

    if do_deblock:
        out = deblock(out, qp)
    return out


# ---------------------------------------------------------------------------
# Deblocking filter (simplified, H.264-style conditional 4-tap smoothing on
# 4x4 block edges).  Mirrors the hardware deblock_4x4.v module bit-exact.
#
# For each 4-pixel edge between two adjacent 4x4 blocks we examine 6 pixels
# straddling the boundary: p2 p1 p0 | q0 q1 q2.  If the gradient across the
# edge is small enough (suggesting it's a coding artifact, not a real edge),
# we replace p0 and q0 with a 3-tap weighted average.
#
# Thresholds are QP-dependent: at low QP the codec is nearly lossless so we
# shouldn't smooth at all; at high QP the quantization noise dominates and
# aggressive smoothing wins.
def _alpha_beta(qp):
    """H.264-style table lookup, simplified."""
    a = max(1, int(qp - 24))
    b = max(1, int((qp - 24) // 2))
    return a, b


def _deblock_edge_inplace(line_p, line_q, qp):
    """Apply the filter on one 4-pixel edge.  line_p[0..2] are the 3 pixels
    on the 'p' side ordered farthest-first (p2,p1,p0); line_q[0..2] is the
    q side ordered nearest-first (q0,q1,q2).  Modifies p0=line_p[2] and
    q0=line_q[0]."""
    alpha, beta = _alpha_beta(qp)
    p2, p1, p0 = int(line_p[0]), int(line_p[1]), int(line_p[2])
    q0, q1, q2 = int(line_q[0]), int(line_q[1]), int(line_q[2])
    if abs(p0 - q0) >= alpha:    return
    if abs(p1 - p0) >= beta:     return
    if abs(q1 - q0) >= beta:     return
    new_p0 = (p2 + 2 * p1 + 2 * p0 + 2 * q0 + q1 + 4) // 8
    new_q0 = (p1 + 2 * p0 + 2 * q0 + 2 * q1 + q2 + 4) // 8
    line_p[2] = max(0, min(255, new_p0))
    line_q[0] = max(0, min(255, new_q0))


def deblock(pixels, qp):
    """Apply deblocking on every 4x4 block edge in `pixels` (H x W uint8).
    Filters vertical edges (between horizontally adjacent blocks) then
    horizontal edges, the same order H.264 specifies."""
    H, W = pixels.shape
    out = pixels.astype(np.int16).copy()

    # Vertical edges: x = 4, 8, 12, ... (between block columns)
    for x in range(4, W, 4):
        for y in range(H):
            p = out[y, x - 3:x].copy()      # p2,p1,p0
            q = out[y, x:x + 3].copy()      # q0,q1,q2
            if len(p) != 3 or len(q) != 3:
                continue
            _deblock_edge_inplace(p, q, qp)
            out[y, x - 1] = p[2]
            out[y, x]     = q[0]

    # Horizontal edges: y = 4, 8, 12, ... (between block rows)
    for y in range(4, H, 4):
        for x in range(W):
            p = out[y - 3:y, x].copy()
            q = out[y:y + 3, x].copy()
            if len(p) != 3 or len(q) != 3:
                continue
            _deblock_edge_inplace(p, q, qp)
            out[y - 1, x] = p[2]
            out[y, x]     = q[0]

    return np.clip(out, 0, 255).astype(np.uint8)


def psnr(a, b):
    a = a.astype(np.float64)
    b = b.astype(np.float64)
    mse = np.mean((a - b) ** 2)
    if mse == 0:
        return float('inf')
    return 10.0 * math.log10(255.0 * 255.0 / mse)


# ---------------------------------------------------------------------------
# Smoke test: generate a small grayscale, encode, decode, PSNR
if __name__ == '__main__':
    import sys
    from PIL import Image

    rng = np.random.default_rng(7)
    if len(sys.argv) > 1 and sys.argv[1] and sys.argv[1] != '-':
        img = np.asarray(Image.open(sys.argv[1]).convert('L'))
    else:
        # Default: a 16x16 gradient + small noise
        img = (np.tile(np.arange(16) * 16, (16, 1)).astype(np.float64)
               + rng.normal(0, 5, (16, 16))).clip(0, 255).astype(np.uint8)

    H, W = img.shape
    H = (H // 4) * 4
    W = (W // 4) * 4
    img = img[:H, :W]

    qp = int(sys.argv[2]) if len(sys.argv) > 2 else 36
    encoded, _ = encode_image(img, qp=qp)
    print(f'encoded {H}x{W} at qp={qp}: {len(encoded)} bytes '
          f'({8 * len(encoded) / (H * W):.3f} bpp)')

    decoded = decode_image(encoded, H, W, qp=qp)
    p = psnr(img, decoded)
    print(f'PSNR: {p:.2f} dB')
