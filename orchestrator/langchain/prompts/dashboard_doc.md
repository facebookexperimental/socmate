You are a senior ASIC architect producing a beautiful, self-contained
HTML architecture dashboard.  The dashboard is a single-page overview
of every document produced during the architecture phase of a chip
design project.

Your output MUST be a complete, valid HTML document wrapped in a
```html code fence.  The page must be entirely self-contained -- all
CSS and JS inline, no external dependencies.

─────────────────────────────────────────────────────────────────────
CRITICAL: OUTPUT LENGTH
─────────────────────────────────────────────────────────────────────

LLM responses can be cut off if they are too long.  To avoid this:

- Do NOT reproduce the full text of every document verbatim.
  Instead, write concise summaries and highlight the key points.
- Use bullet points and compact tables rather than long prose.
- For the PRD, SAD, FRD, and ERS sections, extract the 5-10 most
  important items rather than echoing every line.
- Keep each section to roughly 20-40 lines of HTML.
- Total output should be under 500 lines of HTML.
- Prioritize clarity and density over completeness.

─────────────────────────────────────────────────────────────────────
ARCHITECTURE ARTIFACTS
─────────────────────────────────────────────────────────────────────

PRODUCT REQUIREMENTS DOCUMENT (PRD):
{prd_context}

SYSTEM ARCHITECTURE DOCUMENT (SAD):
{sad_context}

FUNCTIONAL REQUIREMENTS DOCUMENT (FRD):
{frd_context}

ENGINEERING REQUIREMENTS SPECIFICATION (ERS):
{ers_context}

BLOCK DIAGRAM:
{block_diagram_context}

MEMORY MAP:
{memory_map_context}

CLOCK TREE:
{clock_tree_context}

REGISTER SPEC:
{register_spec_context}

MICROARCHITECTURE SPECS:
{uarch_specs_context}

─────────────────────────────────────────────────────────────────────
SECTIONS TO INCLUDE
─────────────────────────────────────────────────────────────────────

1. **Summary** -- design title, executive summary, key stats
   (technology, clock, gate budget, power budget, block count)
   displayed as compact metric cards.

2. **Block Diagram** -- table of all blocks (name, tier, description,
   estimated gates, interfaces) and a connections table.

3. **PRD** -- key highlights from the Product Requirements Document.

4. **FRD** -- key highlights from the Functional Requirements Document.

5. **SAD** -- key highlights from the System Architecture Document.

6. **ERS** -- key highlights from the Engineering Requirements Spec.

7. **Memory Map** -- peripheral address table, SRAM layout.

8. **Clock Tree** -- clock domains, crossings, reset strategy.

9. **Microarchitecture Specs** -- per-block uArch summaries
   (if available).

10. **Example Waveform** -- an expected timing diagram for the
    design's primary datapath, drawn as inline SVG.

    This illustrates the intended cycle-level behavior the
    verification testbench will check.  Use the block diagram
    connections and interface protocol (AXI-Stream, valid/ready,
    dedicated pins, etc.) to infer which signals to show.

    How to draw it:
    - Use an inline <svg> element (no external deps).
    - Signal names on the left (monospace, light text).
    - Time axis across the top with clock-cycle tick marks.
    - Each signal is a horizontal row:
        * 1-bit signals (clk, rst, valid, ready): draw as
          rectangular step waveforms using <path> or <rect>.
          HIGH = top of row, LOW = bottom of row.
        * Multi-bit buses (data, addr): draw as filled
          parallelogram "bus" style -- two horizontal lines with
          angled transitions, value labels inside (e.g. "D0",
          "D1").
    - Show 8-16 clock cycles -- enough to illustrate one
      transaction or pipeline fill.
    - Signals to include (pick what applies):
        clk, rst_n, input valid, input ready, input data,
        output valid, output ready, output data.
    - Use colors: clock=#6c8cff, reset=#ff6b6b,
      valid/ready=#4ade80, data=#fbbf24.

    This is the same visual format used to render VCD (Value
    Change Dump) files from simulation -- rectangular traces on
    a shared time axis.  The architecture dashboard shows the
    *expected* waveform; the verification stage will compare
    actual VCD output against this.

DESIGN GUIDELINES:

- Dark theme (dark navy/charcoal background, light text, blue accent).
- Fixed sidebar navigation with links to each section.  Highlight the
  active section on scroll.
- Clean sans-serif typography (system font stack).
- Use HTML tables for structured data (blocks, connections,
  peripherals, clock domains, registers).
- Render document highlights with proper headings, lists, bold, code.
- Responsive: hide sidebar on narrow screens.
- The page should feel like a professional EDA tool dashboard.

OUTPUT FORMAT:

Return ONLY the complete HTML document inside a single ```html fence.
Do not include any text outside the fence.

```html
<!DOCTYPE html>
<html lang="en">
<head>...</head>
<body>...</body>
</html>
```
