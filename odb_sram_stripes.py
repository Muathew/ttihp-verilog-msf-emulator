# SPDX-License-Identifier: Apache-2.0
"""Rewrite the Metal4 PDN over the SRAM macros (run by Project.ExtendPowerStripes).

On IHP CMOS5L the block's only stripe layer is Metal4, the same layer as the
power pins of RM_IHPSG13_2P_512x16_c2_bm_bist. pdngen cuts each stripe short
of a fixed macro, so the macro gets no supply and every cut stripe becomes two
half-height power ports. For each SRAM instance (orientation R0 or MX) this
script:

1. Takes the macro's supply columns from its Metal4 PIN rectangles: VSS!
   columns are VGND, VDD! and VDDARRAY! columns are VPWR. The 2P_512x16 LEF
   declares every column as a PIN, 4.42 um wide in the two bit-cell arrays
   and 2.81 um wide in the band between them. In an array a VPWR column is
   VDD! below a 6.4 um gap and VDDARRAY! above it; the LEF covers the gap
   with an OBS, but the macro GDS has no Metal4 there (checked with gdstk on
   23 Oct 2026), so a stripe across it only joins VDD! to VDDARRAY!, which
   PDN_MACRO_CONNECTIONS ties to VPWR anyway.
2. Groups the columns into regions (array L, band, array R) at gaps wider
   than REGION_BREAK_UM.
3. Moves every tile stripe that crosses the macro to the nearest free column
   of its own net, gives each chosen column a partner of the other net in its
   region, and makes sure every region has at least one VPWR/VGND pair.
4. Deletes the crossing stripes (and their vias and pin boxes) and draws
   full-height stripes on the chosen columns, with Metal1-rail via stacks
   outside the macro; each new stripe is one full-height power pin box.

Then it checks its own result and fails the step if a power pin box on
Metal4 is not full height, or a Metal4 power shape over a macro is not inside
a column of its own net.

Approach from MarcosAsh/protocol-emulator (odb_sram_stripes.py), which follows
kdp1965/ihp-um-janestreet-prism (odb_stripes.py); both Apache-2.0.
"""
import click
import odb
from reader import click_odb

SRAM_MASTERS = ("RM_IHPSG13_2P_512x16_c2_bm_bist",)
NET_OF_PIN = {"VDD!": "VPWR", "VDDARRAY!": "VPWR", "VSS!": "VGND"}
OTHER = {"VPWR": "VGND", "VGND": "VPWR"}

REGION_BREAK_UM = 12.0   # column gaps: 8.84 in an array, 5.15 in the band, 21.7 between
PAIR_GAP_UM = 9.5        # a VPWR and a VGND column this close make a pair
CROSS_MARGIN_UM = 1.0    # a stripe this close to a macro edge counts as crossing it
MIN_PAIRS = 1            # per region


@click.command()
@click.option("--layer", default="Metal4", help="Vertical PDN layer")
@click_odb
def extend(reader, layer):
    block = reader.block
    tech = reader.tech
    m4 = tech.findLayer(layer)
    if m4 is None:
        raise click.ClickException(f"tech layer {layer} not found")
    dbu = block.getDefUnits()
    core = block.getCoreArea()
    ylo, yhi = core.yMin(), core.yMax()
    full_tol = int(1.0 * dbu)

    def um(v):
        return v / dbu

    def is_vertical_m4(b):
        return (
            b.getTechLayer() is not None
            and b.getTechLayer().getName() == layer
            and (b.yMax() - b.yMin()) > (b.xMax() - b.xMin())
        )

    srams = [
        i for i in block.getInsts()
        if i.getMaster().isBlock() and i.getMaster().getName() in SRAM_MASTERS
    ]
    if not srams:
        print("[INFO] no SRAM macros; nothing to do")
        return
    for inst in srams:
        if inst.getOrient() not in ("R0", "MX"):
            raise click.ClickException(
                f"{inst.getName()}: orientation {inst.getOrient()} unsupported; "
                "R0 or MX keep the Metal4 supply columns vertical"
            )

    # --- 1-2. supply columns per instance, die frame, with regions ---------
    def columns(inst):
        master = inst.getMaster()
        ox = inst.getBBox().xMin()
        cols = {}  # (x0, x1) -> set of nets (one net expected)
        for pin, net in NET_OF_PIN.items():
            mterm = master.findMTerm(pin)
            if mterm is None:
                raise click.ClickException(f"{master.getName()}: no pin {pin}")
            for mpin in mterm.getMPins():
                for box in mpin.getGeometry():
                    if box.getTechLayer() is None or box.getTechLayer().getName() != layer:
                        continue
                    cols.setdefault((ox + box.xMin(), ox + box.xMax()), set()).add(net)
        out = {"VPWR": {}, "VGND": {}}
        ordered = sorted(cols, key=lambda c: c[0] + c[1])
        region, last_cx, names = 0, None, {}
        for c in ordered:
            nets = cols[c]
            if len(nets) != 1:
                raise click.ClickException(f"{inst.getName()}: column {c} on both nets")
            cx = (c[0] + c[1]) // 2
            if last_cx is not None and cx - last_cx > REGION_BREAK_UM * dbu:
                region += 1
            last_cx = cx
            names[c] = region
            out[next(iter(nets))][c] = region
        nreg = region + 1
        if nreg != 3:
            print(f"[WARNING] {inst.getName()}: {nreg} column regions (expected 3)")
        return out

    def centre(c):
        return (c[0] + c[1]) // 2

    # --- existing stripes per net -------------------------------------------
    grid = {}
    for net_name in ("VPWR", "VGND"):
        net = block.findNet(net_name)
        if net is None:
            raise click.ClickException(f"net {net_name} not found")
        grid[net_name] = [
            b for sw in net.getSWires() for b in sw.getWires() if is_vertical_m4(b)
        ]

    margin = int(CROSS_MARGIN_UM * dbu)

    def crossing(boxes, inst):
        ib = inst.getBBox()
        return [b for b in boxes if b.xMax() > ib.xMin() - margin and b.xMin() < ib.xMax() + margin]

    # --- 3. allocation -------------------------------------------------------
    alloc = {}
    legal = {}
    for inst in srams:
        cols = columns(inst)
        legal[inst.getName()] = cols
        chosen = {"VPWR": [], "VGND": []}
        pair_gap = int(PAIR_GAP_UM * dbu)

        def free(nn, region=None):
            return [
                c for c, r in cols[nn].items()
                if c not in chosen[nn] and (region is None or r == region)
            ]

        def partnered(c, nn):
            return any(
                abs(centre(o) - centre(c)) <= pair_gap and cols[OTHER[nn]][o] == cols[nn][c]
                for o in chosen[OTHER[nn]]
            )

        for nn in ("VPWR", "VGND"):
            targets = sorted({centre((b.xMin(), b.xMax())) for b in crossing(grid[nn], inst)})
            for tx in targets:
                cands = free(nn)
                if not cands:
                    print(f"[WARNING] {inst.getName()}: no free {nn} column for x={um(tx):.2f}")
                    continue
                c = min(cands, key=lambda c: abs(centre(c) - tx))
                chosen[nn].append(c)
                print(f"[INFO] {inst.getName()}: {nn} stripe x={um(tx):.2f} -> column "
                      f"x={um(centre(c)):.2f} (region {cols[nn][c]})")

        def complete_pairs():
            for nn in ("VPWR", "VGND"):
                for c in list(chosen[nn]):
                    if partnered(c, nn):
                        continue
                    r = cols[nn][c]
                    cands = free(OTHER[nn], r)
                    if not cands:
                        print(f"[WARNING] {inst.getName()}: no {OTHER[nn]} partner in region {r}")
                        continue
                    o = min(cands, key=lambda o: abs(centre(o) - centre(c)))
                    chosen[OTHER[nn]].append(o)
                    print(f"[INFO] {inst.getName()}: {OTHER[nn]} column x={um(centre(o)):.2f} "
                          f"added to pair x={um(centre(c)):.2f}")

        complete_pairs()
        for r in sorted(set(cols["VPWR"].values()) | set(cols["VGND"].values())):
            if not any(cols["VPWR"][c] == r and partnered(c, "VPWR") for c in chosen["VPWR"]):
                cands = free("VPWR", r)
                if cands:
                    mid = sorted(cands, key=centre)[len(cands) // 2]
                    chosen["VPWR"].append(mid)
                    print(f"[INFO] {inst.getName()}: VPWR column x={um(centre(mid)):.2f} "
                          f"added so region {r} has a pair")
                    complete_pairs()
        alloc[inst.getName()] = {nn: sorted(chosen[nn]) for nn in chosen}
        print(f"[INFO] {inst.getName()}: {len(chosen['VPWR'])} VPWR / "
              f"{len(chosen['VGND'])} VGND column stripes")

    # --- via masters used by pdngen for stripe-to-rail stacks ----------------
    def via_layers(v):
        return (v.getBottomLayer().getName(), v.getTopLayer().getName())

    # --- 4. rewrite geometry -------------------------------------------------
    for net_name in ("VPWR", "VGND"):
        net = block.findNet(net_name)
        swires = list(net.getSWires())
        swire = swires[0] if swires else odb.dbSWire_create(net, "ROUTED")
        all_boxes = [b for sw in swires for b in sw.getWires()]
        rails = [
            b for b in all_boxes
            if b.getTechLayer() is not None and b.getTechLayer().getName() == "Metal1"
            and (b.xMax() - b.xMin()) > (b.yMax() - b.yMin())
        ]
        # one via master per layer pair, as pdngen used them on this net
        stack = {}
        for b in all_boxes:
            if b.isVia() and b.getBlockVia() is not None:
                v = b.getBlockVia()
                stack.setdefault(via_layers(v), v)
        stack = [stack[k] for k in sorted(stack) if k[0].startswith("Metal") and k[0] != layer]
        bpin = None
        for bterm in net.getBTerms():
            pins = list(bterm.getBPins())
            if pins:
                bpin = pins[0]
                break

        for inst in srams:
            ib = inst.getBBox()
            cross = crossing(
                [b for sw in net.getSWires() for b in sw.getWires() if is_vertical_m4(b)], inst
            )
            removed_x = [(b.xMin(), b.xMax()) for b in cross]
            cross_keys = {(b.xMin(), b.yMin(), b.xMax(), b.yMax()) for b in cross}

            def on_removed(box):
                return any(box.xMax() > rx0 and box.xMin() < rx1 for (rx0, rx1) in removed_x)

            for sw in net.getSWires():
                for b in list(sw.getWires()):
                    key = (b.xMin(), b.yMin(), b.xMax(), b.yMax())
                    if (is_vertical_m4(b) and key in cross_keys) or (b.isVia() and on_removed(b)):
                        odb.dbSBox_destroy(b)
            if bpin is not None:
                for box in list(bpin.getBoxes()):
                    if box.getTechLayer() is not None and box.getTechLayer().getName() == layer and on_removed(box):
                        odb.dbBox_destroy(box)

            new_vias = 0
            for c0, c1 in alloc[inst.getName()][net_name]:
                odb.dbSBox_create(swire, m4, c0, ylo, c1, yhi, "STRIPE")
                if bpin is not None:
                    odb.dbBox_create(bpin, m4, c0, ylo, c1, yhi)
                cx = (c0 + c1) // 2
                for r in rails:
                    if r.xMin() <= cx <= r.xMax() and (r.yMax() <= ib.yMin() or r.yMin() >= ib.yMax()):
                        ry = (r.yMin() + r.yMax()) // 2
                        for v in stack:
                            odb.dbSBox_create(swire, v, cx, ry, "STRIPE")
                        new_vias += 1
            print(f"[INFO] {inst.getName()}: {net_name}: removed {len(cross)} crossing segments, "
                  f"drew {len(alloc[inst.getName()][net_name])} column stripes, "
                  f"{new_vias} rail via stacks ({len(stack)} vias each)")

    # --- self-check -----------------------------------------------------------
    errors = []
    for net_name in ("VPWR", "VGND"):
        net = block.findNet(net_name)
        for bterm in net.getBTerms():
            for bpin in bterm.getBPins():
                for box in bpin.getBoxes():
                    if box.getTechLayer() is None or box.getTechLayer().getName() != layer:
                        continue
                    if box.yMin() > ylo + full_tol or box.yMax() < yhi - full_tol:
                        errors.append(f"{net_name} pin box x={um(box.xMin()):.2f}..{um(box.xMax()):.2f} "
                                      f"y={um(box.yMin()):.2f}..{um(box.yMax()):.2f} is not full height")
        for inst in srams:
            ib = inst.getBBox()
            own = legal[inst.getName()][net_name]
            for sw in net.getSWires():
                for b in sw.getWires():
                    if b.getTechLayer() is None or b.getTechLayer().getName() != layer:
                        continue
                    if b.xMax() <= ib.xMin() or b.xMin() >= ib.xMax() or b.yMax() <= ib.yMin() or b.yMin() >= ib.yMax():
                        continue
                    if not any(c0 <= b.xMin() and b.xMax() <= c1 for (c0, c1) in own):
                        errors.append(f"{net_name} {layer} shape x={um(b.xMin()):.2f}..{um(b.xMax()):.2f} "
                                      f"over {inst.getName()} is not on a {net_name} column")
    if errors:
        for e in errors:
            print(f"[ERROR] {e}")
        raise click.ClickException(f"{len(errors)} PDN problems after rewriting the SRAM stripes")
    print("[INFO] SRAM stripes: every power pin is full height and every stripe over a macro is on its own column")


if __name__ == "__main__":
    extend()
