# Flash-Streaming Tiny Transformer Accelerator Requirements

Build a Sky130 Verilog-2005 soft IP block for a host-sequenced transformer
inference accelerator suitable for a Caravel-class user area demo. The design
must target a TinyStories/llama2.c-class model, with INT4 weights stored in
external flash and activations/KV cache stored on chip. Do not attempt to store
meaningful model weights on die.

## Binding Constraint

Memory area is the binding constraint, not compute:

- Sky130 OpenRAM macros are assumed to land around 0.1 to 0.2 Mbit/mm2 in
  practical OpenROAD/OpenRAM flows.
- Caravel-class user area is assumed to be about 10 mm2.
- 32 KB on-chip SRAM is realistic for activation scratchpad.
- 128 KB on-chip SRAM consumes most of the chip and should be avoided.
- 500 KB or more of on-chip SRAM does not fit.

Therefore the architecture must stream weights from external QSPI or Octal
flash. On-chip memory is reserved for activation buffers, small tables, control
state, and a limited KV cache.

## Target Demo

The intended flagship demo is coherent TinyStories text streaming through a
UART, with tokenization performed off chip by the host.

Target model class:

- llama2.c / TinyStories style transformer.
- Primary silicon target: 260K parameter llama2.c model at INT4 weights.
- Stretch analysis target: 1M to 15M parameter models, still INT4 weights.
- 15M parameters at INT4 is about 7.5 MB of weights per decoded token and is
  expected to be memory-bound at 2 to 3 tokens/s with Quad SPI.
- 260K parameters at INT4 is about 130 KB of weights and should reach
  interactive token rates with a faster flash interface.

## Required Architecture

Top-level behavior:

- Host CPU sequences model layers and tokenization.
- Accelerator executes transformer kernels and exposes checkpointable
  intermediate tensor outputs for validation.
- External flash provides all model weights as a streaming source.
- UART or simple host stream provides token and control I/O.

Datapath requirements:

- INT4 weight stream expanded to signed INT8 or INT16 internal lanes.
- INT8 activation input lanes.
- 8x8 or 16x16 INT8 MAC array, parameterized at generation time.
- Weight-stationary or output-stationary matmul dataflow; choose one and
  document the exact reuse and accumulation order.
- INT32 accumulators with deterministic saturation/rounding down to INT8 or
  INT16 outputs.
- Double-buffered weight prefetch from external flash so the compute array can
  overlap useful work with QSPI reads when possible.
- Activation scratchpad target around 32 KB.
- KV cache target around 16 KB, limiting context to roughly 64 to 128 tokens
  for small hidden dimensions.

Transformer operations:

- Bit-exact INT4/INT8 matmul kernel.
- RMSNorm with a documented reciprocal-square-root implementation, using
  either LUT or fixed-iteration Newton refinement.
- SiLU activation with a documented LUT or piecewise approximation.
- Rotary position embeddings on Q/K with a compact sin/cos LUT.
- Attention softmax with streaming max, exp LUT, sum, reciprocal, and
  normalization. Treat softmax as a dedicated high-risk block.
- Host-visible intermediate tensor checkpoints after matmul, RMSNorm, RoPE,
  softmax, attention output, and MLP output.

Interfaces:

- External weight interface: QSPI minimum; Octal flash or parallel NOR may be
  represented as a parameterized wider variant.
- Host/control interface: simple memory-mapped register or command stream.
- Token I/O: UART-compatible byte stream or host stream. Tokenizer is not in
  RTL.
- All major block-to-block streams should use valid/ready handshakes.

## Verification Requirements

Verification must be staged:

1. Bit-exact matmul against a Python/PyTorch or NumPy golden model.
2. Bit-exact RMSNorm, RoPE, SiLU, and softmax primitive tests.
3. Bit-exact single transformer block against golden vectors exported from a
   PyTorch/llama2.c-style reference.
4. End-to-end host-sequenced decode smoke test on a tiny fixed model.

Validation DV must verify every ERS requirement and must include application
intent KPIs:

- `matmul_bit_exact`: for at least 64 randomized matrix tiles, RTL output must
  exactly match the fixed-point golden model.
- `single_block_bit_exact`: for at least one exported transformer block vector,
  all checkpoint tensors must match the golden model within the documented
  fixed-point tolerance.
- `flash_bandwidth_model`: QSPI bandwidth accounting must report expected
  token latency from model size and interface width. For 15M INT4 weights at
  realistic 15 MB/s flash throughput, the reported lower-bound memory time must
  be at least 400 ms/token. For 260K INT4 weights, the reported lower-bound
  memory time must be no more than 20 ms/token at the same bandwidth.
- `onchip_memory_limit`: generated architecture must budget no more than 64 KB
  combined activation and KV SRAM by default.
- `no_onchip_weight_storage`: architecture and RTL must not allocate storage
  for more than one prefetched weight tile plus small metadata.

All simulation testbenches must dump VCDs and run WaveKit signal audits on key
control and dataflow contracts.

## Risks To Address Explicitly

- Softmax is the highest-risk block. It must get a dedicated microarchitecture
  and verification plan.
- RoPE sin/cos LUT addressing and fixed-point scaling must be explicitly
  documented.
- RMSNorm reciprocal-square-root approximation must be documented and tested.
- The flash interface must not be sketched as an afterthought; it is the
  throughput limiter and must expose backpressure and bandwidth counters.

## Preferred Decomposition

Prefer a host-sequenced transformer inference unit over a monolithic "LLM on a
chip." The host should be able to checkpoint every layer output. The same RTL
can still run the 260K TinyStories demo, but this framing produces cleaner
verification and a more credible tape-out target.
