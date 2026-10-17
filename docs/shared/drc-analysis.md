# DRC Failure & 3D Rendering Analysis — tt_um_msf

> **Correction (2026-10-17).** Several numbers below were wrong and are fixed
> inline: 6x4 *tiles* is ~1.21 x 0.62 mm = **~0.75 mm²** (not 6 x 4 mm =
> 24 mm²); the SRAM macro outline 402.61 x 219.77 µm is **~0.088 mm²** (not
> 0.88 mm²); the "31K cells" figure dates from the flip-flop SRAM build — after
> the macro integration the logic was ~2.3K generic gates, and the v2 design
> (threads, SERDES, CRC) maps to the figure reported by `tools/synth_check.py`
> (see docs/sam/PROGRESS.md). The GDS analysed here (2026-09-30) predates the
> current RTL and is no longer in the repository; CI regenerates it.

> Updated 2026-09-30. Based on `tt_um_msf.gds` (18.3 MB, 204 671 boundaries, 66 074 structures).

## 1. Why the SRAM looks "floated / plopped in" in GDS3D

The `RM_IHPSG13_2P_512x16_c2_bm_bist` macro is a **fixed-size blackbox** (402.61 × 219.77 µm,
~0.088 mm²) placed by OpenROAD at `[42, 160]` µm with `R90` orientation. In the 3D view it
appears as a dense rectangular block sitting on top of the standard-cell forest. Three
things make it look "floating":

| Cause | Detail |
|---|---|
| **Visual contrast** | The macro is a solid, dense SRAM array (6 144 bit-cells). Surrounding standard cells are sparse rectangles. The macro's block shape is visually distinct — it *looks* dropped in, but it's correctly placed. |
| **R90 rotation** | The macro is rotated 90° relative to the standard-cell rows. This makes it visually "off-axis" from the surrounding logic, reinforcing the "plopped in" impression. |
| **Power halo not visible** | The macro's `VDD!`, `VSS!`, `VDDARRAY!` pins are on **Metal4**. The `pdn_cfg.tcl` connects `Metal4 → TopMetal1` (the macro PDN grid). If the TopMetal1/TopMetal2 stripes aren't rendered (or are hidden with Ctrl+scroll), the power "halo" around the macro disappears and the block looks disconnected. |

**Not a bug.** The macro is correctly placed and connected. The 3D viewer just doesn't
show the Metal4→TopMetal1 via connections at the macro boundary at typical zoom.

## 2. Why there are "wires going to nothing"

| Likely cause | Evidence |
|---|---|
| **IO pads at die edges** | The design has 8 bidirectional pins (`ui_in[7:0]`, `uo_out[7:0]`, `uio_in[7:0]`, `uio_out[7:0]`, `uio_oe[7:0]`). The metal routing connects to the TT IO pad structures at the die boundary. The pads themselves are on GDS layers that may not be in `tt_um_msf.txt` (our process file covers layers 1–134; the TT IO pad layers may be outside this range). GDS3D renders unlisted layers with default geometry, so the pad structures can look like "wires to nothing." |
| **DRC violation stubs** | `review.md` confirms **unresolved DRC violations (shorts & spacing)**. When the P&R tool can't route cleanly (because the design is over area budget — see §3), it can leave **dangling wire segments** (stubs) that connect to nothing. These are real GDS geometry, not a viewer artifact. |
| **Viewer layer gaps** | Our `tt_um_msf.txt` doesn't define every layer in the GDS. The verbose log showed ~15 "Layer X not in process" notices. Layers that aren't in the process file get default rendering (often invisible or mis-heighted), so some routing can appear disconnected. |

**Quick check in GDS3D:** press **L** to open the legend, enable all layers, then use
**Ctrl+scroll** to strip layers from the top. If the "wires to nothing" disappear when you
hide a specific layer, that layer is the culprit.

## 3. Root cause: design is over the 24K cell budget

| Metric | Value |
|---|---|
| Die size | 6 × 4 tiles ≈ 1.21 × 0.62 mm ≈ 0.75 mm² |
| Budget | ~24K logic cells |
| Synthesized design | ~31K cells *with the old flip-flop SRAM*; ~2.3K generic gates after the macro (stale claim, see note) |
| Over budget | **~7K cells (29 % over)** |

The P&R tool (OpenROAD) was forced to **squeeze 31K cells into a 24K-cell area**. This
caused:

1. **DRC violation loop** — the tool kept trying to place/route, created shorts and
   spacing violations, and couldn't converge. The GDS it produced has **unresolved
   DRC errors** (confirmed in `review.md`).
2. **Sparse standard-cell placement** — to fit the SRAM macro (0.88 mm²) plus 31K
   standard cells into 24 mm², the tool spread the cells thin, leaving visible gaps
   ("so much space").
3. **Routing stubs** — incomplete routes between cells and the SRAM macro, and between
   cells and IO pads, because the tool ran out of routing resources.

### The SRAM macro is the main area consumer

The `RM_IHPSG13_2P_512x16_c2_bm_bist` macro is a **512×16 2-port SRAM** with BIST.
In the old FF-based build (before macro integration), this would have cost ~6 000
flip-flops. The macro replaces that with a fixed 0.88 mm² block. The `updates.md`
confirms the SRAM was integrated in the 30/9 update, resolving the FF blowup, but the
*remaining* 31K standard cells are still over the 24K budget.

## 4. What to fix (prioritised)

| # | Action | Impact |
|---|---|---|
| 1 | **Reduce logic** — the 31K → 24K gap is ~7K cells. The `PLAN.md` budget was ~8K cells for the core (F1.5 footprint). The shift datapath, 2-stage pipeline, and BIST control are likely the area hogs. Consider: drop BIST from the SRAM macro (use the non-BIST variant), simplify the ctrl FSM, or reduce IMEM from 512 to 256 words. | Removes the over-budget root cause |
| 2 | **Re-run P&R** after logic reduction — the current GDS has unresolved DRC. A clean P&R run with the design under budget should eliminate the stubs and spacing violations. | Fixes the "wires to nothing" |
| 3 | **Add missing layers to `tt_um_msf.txt`** — the TT IO pad layers and any TopMetal layers not yet in the process file. Check the GDS layer list (use KLayout or `gdsread`) and add the pad layers. | Makes IO pads visible in GDS3D |
| 4 | **Verify PDN rendering** — enable TopMetal1/TopMetal2 in GDS3D (Ctrl+scroll down to un-hide). The macro's power connections should become visible, eliminating the "floating" look. | Cosmetic |

## 5. GDS3D process file notes

`docs/shared/gds3d/tt_um_msf.txt` (created 2026-09-30) defines 20 layers based on the
`ihp-sg13g2` PDK from `pdk_layers.js`. Known gaps:

- **IO pad layers** not defined (need to check which GDS layers the TT pads use).
- **TopMetal1/TopMetal2** (layers 126/134) are defined but may need `Show: 1` verified.
- **Layer 63** (datatype 0 and -1) appears in the GDS but isn't in the process file —
  likely a PDK-specific layer (diffusion? implant mask?).
- **Layer 189** (datatype 4) — unknown, likely a PDK annotation layer.
