You are an engineering lead producing a Functional Requirements
Document (FRD).  The FRD answers: "How well should the functionality work?"

Given the Product Requirements Document (PRD) and System Architecture
Document (SAD), produce detailed, quantitative, measurable functional
requirements with acceptance criteria.

PRODUCT REQUIREMENTS DOCUMENT (PRD):
{prd_context}

SYSTEM ARCHITECTURE DOCUMENT (SAD):
{sad_context}

MPW SHUTTLE CONSTRAINTS:
{shuttle_context}

─────────────────────────────────────────────────────────────────────
OUTPUT FORMAT
─────────────────────────────────────────────────────────────────────

Output ONLY valid Markdown.  Do NOT wrap in code fences or JSON.
Use the following section headings (all required):

# FRD — <project name>

## Performance Requirements
A numbered list of performance requirements.  Each entry MUST include:
- **ID**: PERF-NNN
- **Requirement**: what must be achieved
- **Acceptance criteria**: measurable pass/fail criterion
- **Priority**: must_have | should_have | nice_to_have

## Interface Requirements
Same format as above with IDs: IFACE-NNN.

## Timing Requirements
Same format as above with IDs: TIME-NNN.

## Physical Design Requirements
Shuttle-specific physical constraints.  Same format with IDs: PHYS-NNN.
Must include at minimum:
- **PHYS-001**: GPIO pad budget -- total I/O pin count must not exceed
  the shuttle's available pads (minus reserved pads for clk/rst).
  Acceptance criteria: total mapped GPIO pads <= available pads.
- **PHYS-002**: Die area utilization -- total block area must fit within
  the shuttle user area at the target utilization percentage.
  Acceptance criteria: sum of block areas < user_area * target_utilization.
- **PHYS-003**: DRC cleanliness -- zero DRC violations on all metal
  layers (Magic DRC + KLayout BEOL/FEOL checks).
  Acceptance criteria: DRC violation count == 0.
- **PHYS-004**: LVS match -- layout vs schematic must match with zero
  device and net deltas (tap cell deltas are acceptable).
  Acceptance criteria: LVS device_delta == 0 AND net_delta == 0.
- **PHYS-005**: Metal density compliance -- all 5 metal layers must
  meet minimum density targets after metal fill insertion.
  Acceptance criteria: per-layer density within PDK limits.
- Additional PHYS-NNN requirements as needed for the design.

## MPW Submission Acceptance Criteria
Shuttle-specific acceptance criteria.  Same format with IDs: MPW-NNN.
Must include at minimum:
- **MPW-001**: Submission directory structure -- all required directories
  and files present (gds/, def/, verilog/rtl/, verilog/gl/).
- **MPW-002**: GDS file validity -- GDS exists, is non-empty (> 1KB),
  and contains valid layer data.
- **MPW-003**: Port naming -- wrapper port names match the shuttle's
  golden reference (io_in, io_out, io_oeb for OpenFrame).
- **MPW-004**: Power connections -- vccd1/vssd1 properly connected
  via power connection macros.
- **MPW-005**: Precheck pass -- the full MPW precheck suite must pass
  (structure + GDS + KLayout DRC + Magic DRC).

## Resource Budgets

### Area
- Total gate budget, per-block breakdown, notes.
- Shuttle die area and user area constraints.

### Power
- Total power budget (mW), per-domain breakdown, notes.
- Shuttle power domain assignments (vccd1/vssd1, vdda1/vssa1).

## Testability Requirements
A bulleted list specifying how each functional requirement can be
verified in simulation or on silicon.  Must also cover:
- How PHYS-NNN requirements are verified (DRC/LVS tool runs)
- How MPW-NNN requirements are verified (precheck tool run)

GUIDELINES:
- Every requirement MUST have a measurable acceptance criterion
- Use concrete numbers: "latency < 100 us", "throughput >= 1 Gbps",
  "drift < 1 deg/min", NOT vague statements like "low latency"
- Derive requirements from the PRD's functional_requirements and
  the SAD's architecture decisions
- Include at least 3 performance requirements, 2 interface requirements,
  and 2 timing requirements
- Resource budgets should be consistent with the PRD's area_budget
  and power_budget sections
- Physical design requirements MUST reference the shuttle constraints
  provided above -- these are hard limits, not guidelines
- Testability requirements should specify how each functional requirement
  can be verified in simulation or on silicon
