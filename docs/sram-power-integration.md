# SRAM power integration on 8x4: the VPWR/VGND short and its fix

Written 15 Nov 2026 on branch `v4`. It covers the failed local hardening run of
13-14 Nov (kept in `harden-failure.tar.gz` and `runs/wokwi`), what was actually wrong,
the fix, and the re-run that checks it (`src/runs/sramfix`). The constraints were the
shuttle's: the PDK (IHP Open PDK `ihp-sg13cmos5l`, commit `89d42ee6`, LibreLane
3.1.0.dev4's pin) and the 8x4 tile (1724.16 x 710.64 µm) stay as they are. Neither was
changed.

## 1. Summary

| | Failed run (`runs/wokwi`) | Fixed run (`src/runs/sramfix`) |
|---|---|---|
| SRAMs at | y = 37.8 µm | y = 3.78 µm (core bottom edge) |
| Router keep-out over SRAMs | none | Metal3 + Metal4 `ROUTING_OBSTRUCTIONS` |
| Standard cells under/level with an SRAM | 1,101 | 0 |
| Global-routing overflow | 12,129 | 0 |
| Global-routing time | 1 h 20 min | 23 s |
| Detailed-routing violations at the end | 4,972 (4,487 shorts) | 0 |
| ... of them over an SRAM / on VPWR or VGND | 4,918 / 3,126 | 0 / 0 |
| Detailed-routing time | 19 h 29 min | 30 min |
| Antenna violations after repair | 0 | 0 |
| Magic extraction | "Ports VPWR and VGND are electrically shorted" | no short reported |
| LVS | 99 errors, top-level pin match failed | 0 errors, "Circuits match uniquely" |
| Magic illegal overlaps | 722 (obsm3/metal3, obsm4/metal4, via) | 84, all in the SRAM's VDD!/VDDARRAY! split band (section 7) |
| Magic DRC outside the SRAMs | 8 | 0 |
| Setup slack, slow corner (30 ns sign-off) | +6.39 ns | +8.90 ns |
| Hold slack, fast corner | +0.23 ns | +0.31 ns |
| Setup / hold violations | 0 / 0 | 0 / 0 |
| Wirelength, global / detailed route | 3.02 m / 2.23 m | 2.17 m / 1.68 m |
| Standard cells, utilisation | 31,877, 51.6 % | 29,034, 47.6 % |
| Whole flow, this machine | about 22 h | about 1 h 30 min (43 min of it Magic DRC) |

The failed run's timing was measured on a layout with shorts, so its slack numbers are
only a rough guide.

**In one sentence:** the SRAM power columns were wired correctly. The short came from
signal wires that the router had to push across the SRAMs, because placement had put
1,101 cells in a row strip under the macros that could reach the rest of the design only
by crossing them.

## 2. What the failed run showed

The flow "completed", but the checkers it does not stop on reported:

* 422,326 Magic DRC errors, 722 Magic illegal overlaps, 99 LVS errors.
* Netgen: `Final result: Top level cell failed pin matching`. Both of the schematic's VPWR
  and VGND land on one layout node (`wire13004/X`, a signal net).
* Magic extraction log: `Ports "VPWR" and "VGND" are electrically shorted.` In the
  extracted netlist, every SRAM's `VDD`, `VDDARRAY` and `VSS` ports are on the same node.
* GeneratePDN: 264 "Unconnected shape" errors on the SRAM Metal4 columns.

That last item looked like the cause ("the SRAM rails do not line up with the chip's"),
but it is not. It is reported *before* `Project.ExtendPowerStripes` runs, which is the step
that connects those columns. This is why the config sets `ERROR_ON_PDN_VIOLATIONS: 0`.
The new run prints the same messages (PSM-0069) at the same point.

## 3. Diagnosis, step by step

Each step ruled out a cause, or confirmed one. The reproducible check is
`tools/sram_pdn_check.py` (section 6).

1. **The macro is consistent with itself.** The SRAM's GDS Metal4 (layer 50/0) has 32
   full-height columns at exactly the LEF pin positions. Its labels (`VDD!` x24,
   `VDDARRAY!` x16, `VSS!` x24) match the LEF pins. There is no hidden Metal4 strap that
   a stripe could short to.
2. **The power grid in OpenROAD's database is correct.** On the final ODB, after the
   stripe step and at the end, no VPWR shape (stripe, via or pin box) overlaps a VGND
   shape on any layer. All 256 SRAM supply-pin shapes are on the right net (VDD! and
   VDDARRAY! on VPWR, VSS! on VGND). `odb_sram_stripes.py`'s own self-check also passed.
3. **Magic reads the SRAM as its LEF abstract** (`MAGIC_EXT_ABSTRACT_CELLS`), and the
   abstract's three supply ports stay separate. So the merge happens in the top-level
   wiring, not inside the macro.
4. **Detailed routing ended with 4,972 violations**, and the flow carried on regardless
   (`GRT_ALLOW_CONGESTION` is in the template's fixed section). 4,918 of these are inside
   SRAM footprints: 3,831 Metal4 shorts, 583 Metal3 shorts and 471 Metal4 spacing
   errors. 3,126 involve VPWR or VGND. One example: `net1019` runs horizontally on
   Metal4 across `sram0` at y = 179.38 µm and touches a VPWR column (x 511.8-516.2) and a
   VGND column (x 520.6-525.0). That single wire joins the two supplies. The count
   started at about 5,068 and barely moved over 16 iterations, so the router could not
   repair it.
5. **Why the router went over the SRAMs.** With the macros at y = 37.8, the default
   10 µm macro halo left a 24 µm strip of rows under them, from y = 3.78 to 27.8.
   Placement put 1,101 logic cells there. Of the 108 signal nets with violations over an
   SRAM, 98 join a cell in that strip to logic above the macros. The only way up is
   across the SRAM. There, Metal2 and Metal3 are mostly LEF obstruction, and the Metal4
   gaps between supply columns are slivers narrower than a track. Global routing
   overflowed (12,129), and detailed routing laid wires across the columns.

## 4. "The rails do not align": what is true and what is not

The tile's Metal4 stripe grid (pitch 50 µm, width 2.1 µm) cannot line up with this
macro's supply columns, and no choice of pitch and offset would fix that.

* The left bit-cell array's VSS! columns sit at 17.195 + 17.68·k µm.
* The right array's VSS! columns are 3.06 µm off that lattice (phase 14.135).
* The band between the two arrays has irregular 2.81 µm columns.
* The 410.6 µm step between macros adds another 3.96 µm phase shift per macro.
* A 2.1 µm stripe stays inside a 4.42 µm column only within ±1.16 µm of its centre.

Ken Pettit's guide
([ihp_sram_macro.html](https://kdp1965.github.io/ihp-um-janestreet-prism/ihp_sram_macro.html))
phases the grid onto the columns for a different macro (1P 512x32: pitch 44.96 µm,
spacing 3.52 µm, offset 6.15 µm). Even there, his stripe step has to nudge stripes onto
the irregular middle band and the right array. Here, `odb_sram_stripes.py` does that for
every stripe: each stripe crossing a macro moves to the nearest free column of its own
net, gets a partner of the other net, and every column region gets at least one pair.
The result is 8, 12, 9 and 8 VPWR/VGND pairs on sram0-3. Section 3 step 2 shows that
this works. **The alignment step was correct and is unchanged.**

## 5. The fix (`src/config.json`)

### 5.1 SRAMs on the core's bottom edge

All four `MACROS` instances move from y = 37.8 to y = 3.78 (the core's lower edge), with
x and orientation (MX, pins facing up) unchanged. With nothing below the macros, no cell
can be stranded there. The 10 µm halo already removes the rows in the 8 µm gaps between
macros, and the side strips (x < 32, x > 1686) reach the logic without crossing an SRAM.
Ken's PRISM places its SRAMs the same way, at y = 3.78.

* **Rejected: keep y = 37.8 and widen `FP_MACRO_VERTICAL_HALO` to about 34 µm.** That
  would empty the strip, but the halo applies on every side. It would also cut 34 µm of
  rows above the macros, where the logic and the macros' pins are, and waste the strip.
* **Rejected: placement blockages for the strip.** This is the same effect with more
  configuration, and the 34 µm stays unused.

The SRAM pins stay facing up, toward the logic, and the logic area above the macros
grows by the 34 µm the macros moved down.

### 5.2 Router keep-out over the SRAMs

```json
"ROUTING_OBSTRUCTIONS": [
  "Metal3 <x0> 3.78 <x1> 221.55",   // per SRAM: stops 2 µm short of the pin edge
  "Metal4 <x0> 3.78 <x1> 223.55",   // per SRAM: whole footprint
  ...
]
```

These are a second line of defence. Even with no demand to cross the SRAMs, a congested
router could use the sub-track gaps in the LEF obstruction. With these, global routing
sees no capacity there and would report overflow instead of quietly shorting a supply.

* **Metal4 covers the whole footprint.** `Odb.AddRoutingObstructions` runs *after*
  `OpenROAD.GeneratePDN` and `Project.ExtendPowerStripes`, so the column stripes are
  already drawn and are not affected. `Odb.RemoveRoutingObstructions` removes the
  obstructions before streamout, so they never reach the GDS.
* **Metal3 stops 2 µm short of the pin edge** (y = 223.55), so Via2 can still land on the
  Metal2 pins there. Ken's obstructions leave a similar gap: they end at y = 192.5 on a
  macro whose top is at 195.12.
* **Metal2 is left open.** It is the pin layer, and the LEF already obstructs the
  macro's interior on Metal2.

### 5.3 What was not changed, and why

* **PDK, tile size, die area, template DEF, pin positions:** the competition's
  constraints.
* **`odb_sram_stripes.py`, `librelane_plugin_msf_pdn.py`, `pdn_cfg.tcl`,
  `PDN_MACRO_CONNECTIONS`:** correct (section 3 step 2, section 4).
* **`FP_PDN_VPITCH` / `VOFFSET`:** no regular grid fits this macro (section 4), and the
  relocation step makes it unnecessary.
* **The template's fixed section** (`GRT_ALLOW_CONGESTION`, `RUN_KLAYOUT_DRC: 0`,
  ...): marked "do not change". Section 7 explains why they hid this problem.
* **`MAGIC_MACRO_STD_CELL_SOURCE`, `ERROR_ON_MAGIC_DRC: false`:** Magic DRC is
  dominated by the SRAM's internals (section 7). It is not part of this fix.

## 6. How to check it

Local hardening, from the repository root (WSL, Docker; the PDK under
`~/ttsetup/pdk/ciel`):

```sh
python -m librelane --pdk-root ~/ttsetup/pdk/ciel --docker-no-tty --dockerized \
    --pdk-root ~/ttsetup/pdk/ciel --pdk ihp-sg13cmos5l --run-tag sramfix --overwrite \
    src/config_merged.json
```

Notes:

* `--docker-no-tty` must come before `--dockerized`.
* `src/config_merged.json` is `config.json` updated with `user_config.json`, as
  `tt/project.py:create_merged_config` builds it. `tt_tool.py --create-user-config` needs
  `yowasp-yosys` installed.
* Without `--force-run-dir` the run lands in `src/runs/<tag>`.

Then the SRAM check (exits non-zero on any failure):

```sh
docker run --rm -v $PWD:$PWD -w $PWD ghcr.io/librelane/librelane:3.1.0.dev4 \
    openroad -python -exit tools/sram_pdn_check.py \
    src/runs/sramfix/final/odb/tt_um_msf.odb \
    src/runs/sramfix/46-openroad-detailedrouting/tt_um_msf.drc
```

On the failed run it prints `placement: 1101 ...`, `routing: 4918 violations over SRAMs,
3126 of them on VPWR/VGND` and `RESULT: FAIL`. On the fixed run it prints zeros and
`RESULT: PASS`. Both results were checked on 15 Nov.

Also read these on any run: `route__drc_errors` and `design__lvs_error__count` in
`final/metrics.json` (both 0 on the fixed run), and the last line of
`*-netgen-lvs/reports/lvs.netgen.rpt` ("Circuits match uniquely").

## 7. Lessons, and what is still open

* **The flow does not stop on these errors.** `GRT_ALLOW_CONGESTION: 1`,
  `ERROR_ON_MAGIC_DRC: false` and LVS ("deferred") let a run with thousands of shorts
  finish and write a GDS. After any hardening run, read `route__drc_errors`, the
  "Ports ... shorted" line in `*-magic-spiceextraction/*.log` and the LVS checker's
  count, or run `tools/sram_pdn_check.py`. "Flow complete" is not enough.
* **The Magic DRC count is mostly noise.** About 410k of the 422k errors in the failed
  run are inside the SRAMs: Magic's CMOS5L rules applied to a foundry bit-cell array
  ("Can't overlap those layers", M2 minimum area, latch-up tie rules). The rest inside
  the SRAMs were spacing errors from the forced routes, and only 8 were outside the
  SRAMs. The shuttle's precheck uses KLayout's CMOS5L deck on the final GDS, as Ken's
  guide does (`RUN_MAGIC_DRC: 0`, `RUN_KLAYOUT_DRC: 1`). That KLayout check is the one
  to trust. **It passed (section 8): 0 violations in the full SG13CMOS5L deck.**
* **The 84 illegal overlaps that remain are expected.** All are `obsm4`/`metal4` at
  y = 170.40-176.25 µm, which is the LEF obstruction band between each column's
  VDD! and VDDARRAY! segments (macro y 47.305-53.15, mirrored by MX). A VPWR column
  stripe crosses that band and joins two pins that are both VPWR. The GDS has no
  Metal4 there (section 3 step 1), and LVS matches. Ken Pettit's guide reports the
  same benign overlap (22 on his macro).
* **Magic DRC after the fix:** 416,536 errors, every one inside an SRAM footprint, and
  0 outside.
* **Not checked yet:** the 48 MHz (20 ns) target (`plan-v4.md` 7.3). This layout signs
  off at 30 ns, as configured. The gate-level tests pass (section 8.3).

## 8. Precheck and CI results (15 Nov)

### 8.1 Tiny Tapeout precheck, run locally

`tt/precheck/precheck.py` was run on the `sramfix` GDS. It used the repository's pinned
requirements (`tt/precheck/requirements.txt`) in a Python 3.12 venv. KLayout 0.30.12 came
from the LibreLane 3.1.0.dev4 image, through a small `klayout` wrapper script that calls
`docker run`. Use an absolute `--gds` path: the Verilog syntax check mounts that path in
a YoWASP sandbox and fails on a relative one.

| Check | Result |
|---|---|
| Pin label, pin check, analog pins | pass |
| KLayout DRC, full SG13CMOS5L deck (`drc_sg13cmos5l.xml`) | pass, 0 items |
| Zero-area shapes, pin label/drawing overlap | pass, 0 items |
| KLayout checks, boundary, layers, cell names | pass |
| Verilog syntax of the gate-level netlist | pass |

So the 416,536 Magic DRC errors (section 7) are not seen by the deck the shuttle uses.

### 8.2 CI GDS run

[Run 43740378700](https://github.com/Muathew/ttihp-verilog-msf-emulator/actions/runs/43740378700),
`gds.yaml` on `v4` at 38421ee, used `tt-gds-action` on a 4-core GitHub runner. The flow
took 3 h 01 min (17:19-20:20 UTC). The same flow took 1 h 23 min locally on 16 cores.

| | CI | local (`sramfix`) |
|---|---|---|
| Flow | complete | complete |
| Detailed routing | 0 violations (after antenna repair) | 0 |
| Antenna | pass | pass |
| LVS | pass | pass, "Circuits match uniquely" |
| Setup and hold, all corners | no violations | no violations |
| Magic DRC | 416,536 (all inside the SRAMs) | 416,536 |
| Illegal overlaps | 84 | 84 |

The CI run matches the local one in every count above.

**The job is marked failed for a reason unrelated to the layout.** After "Flow complete",
`tt-gds-action` uploads the logs. GitHub refused with `Failed to CreateArtifact: Artifact
storage quota has been hit` ([v4-open-problems](v4-open-problems.md) A2). The account
holds 755 MB of artifacts from `main` and `plan7`. The other uploads were skipped, the job
failed, and the `precheck`, `gl_test` and `viewer` jobs were skipped because they depend
on it. The CI precheck is covered by 8.1. The gate-level tests were then run locally
(8.3). Free the artifact store (delete the old
`GDS_logs`, `test-results` and `gds_render` artifacts, and lower the retention period),
wait for GitHub to recalculate usage, then rerun `gh workflow run gds.yaml --ref v4`.

**Artifacts cleared (15 Nov, 22:30 UTC):** all 16 artifacts were deleted, on Muath's
choice. They were older CI outputs from `main` and `plan7`, and nothing has been
submitted from them. CI was rerun on `v4` at 405f022
([run 43769989922](https://github.com/Muathew/ttihp-verilog-msf-emulator/actions/runs/43769989922)).

### 8.3 Gate-level tests, run locally

`cd test && GATES=yes make` was run on `final/nl/tt_um_msf.nl.v` from `sramfix`, which
is the netlist the TT `gl_test` action uses (the `nl`, not the `pnl`). It used cocotb
2.0.1 (`test/requirements.txt`) and the PDK's `sg13cmos5l_stdcell.v`/`_udp.v`/`_io.v` at
the pinned commit.

| Test | Result | Simulated time |
|---|---|---|
| `test_gl_smoke.test_gl_elaboration_smoke` | pass | 8.9 µs |
| `test_gl_smoke.test_gl_reset_recover` | pass | 9.9 µs |
| `test_gl_firmware.test_gl_uart_tx` | pass | 2.17 ms |
| `test_gl_firmware.test_gl_two_threads` | pass | 0.73 ms |
| `test_gl_firmware.test_gl_spi_flash` (store at SRAM 1536) | pass | 2.93 ms |

The run took 5 minutes, and `cocotb_tools.check_results` reported 5 of 5 passed. This
is the first gate-level run of `test_gl_spi_flash` since the flash store moved to 1536
(`v4-bugs.md` F1).

**Use Tiny Tapeout's Icarus 13.0.** Stock Icarus 12.0 (Ubuntu's package) fails all five
tests with X on every output. The CMOS5L flip-flop models clock data through the
`delayed_CLK`/`delayed_D` signals of their `$setuphold` checks. Icarus 12 does not drive
those signals ("will not be driven", 138 warnings), and `-gspecify` does not help. The
`gl_test` action installs `iverilog_13.0-1_amd64.deb` from TinyTapeout/iverilog, which
drives them. After switching versions, delete `test/sim_build/gl` (`make clean` does not
remove it): a `.vvp` compiled by 12.0 will not run on 13.0.
