# SPDX-License-Identifier: Apache-2.0
"""Check a hardened run's SRAM power integration (docs/sram-power-integration.md).

Run with OpenROAD's Python, e.g. from the LibreLane image:

    docker run --rm -v $PWD:$PWD -w $PWD ghcr.io/librelane/librelane:3.1.0.dev4 \
        openroad -python -exit tools/sram_pdn_check.py runs/<tag>/final/odb/tt_um_msf.odb \
        [runs/<tag>/NN-openroad-detailedrouting/tt_um_msf.drc]

It reports, and exits non-zero on, the three things that went wrong on 13-14 Nov 2026:

1. Rails: any VPWR shape (stripe, via, pin box) overlapping a VGND shape on the same
   layer, and any SRAM supply-pin shape (VDD!, VDDARRAY!, VSS!) not on its net
   (VDD!/VDDARRAY! -> VPWR, VSS! -> VGND).
2. Placement: standard cells below an SRAM or level with one inside its x range
   (cells there can only reach the logic by routing across the SRAM).
3. Routing (if the detailed-routing .drc report is given): violations over an SRAM
   footprint, and those that involve VPWR or VGND.
"""
import collections
import re
import sys

import odb

PIN_NET = {"VDD!": "VPWR", "VDDARRAY!": "VPWR", "VSS!": "VGND"}
FILLERS = ("fill", "decap", "tap")


def main(odb_path, drc_path=None):
    db = odb.dbDatabase.create()
    odb.read_db(db, odb_path)
    blk = db.getChip().getBlock()
    u = blk.getDefUnits()
    srams = [i for i in blk.getInsts() if i.getMaster().isBlock()]
    boxes = [i.getBBox() for i in srams]
    fails = 0

    # 1. rails ---------------------------------------------------------------
    shapes = collections.defaultdict(list)  # layer -> [(rect, net, what)]
    for name in ("VPWR", "VGND"):
        net = blk.findNet(name)
        for sw in net.getSWires():
            for b in sw.getWires():
                if b.isVia():
                    v = b.getBlockVia() or b.getTechVia()
                    cx, cy = b.getViaXY()
                    for vb in v.getBoxes():
                        shapes[vb.getTechLayer().getName()].append(
                            ((cx + vb.xMin(), cy + vb.yMin(), cx + vb.xMax(), cy + vb.yMax()), name, "via"))
                else:
                    shapes[b.getTechLayer().getName()].append(
                        ((b.xMin(), b.yMin(), b.xMax(), b.yMax()), name, "stripe"))
        for bt in net.getBTerms():
            for bp in bt.getBPins():
                for b in bp.getBoxes():
                    shapes[b.getTechLayer().getName()].append(
                        ((b.xMin(), b.yMin(), b.xMax(), b.yMax()), name, "pin"))
    wrong_net = 0
    for inst in srams:
        bb, h, o = inst.getBBox(), inst.getMaster().getHeight(), inst.getOrient()
        if o not in ("R0", "MX"):
            print(f"[FAIL] {inst.getName()}: orientation {o} (supply columns need R0 or MX)")
            fails += 1
            continue
        for it in inst.getITerms():
            mt = it.getMTerm()
            if mt.getName() not in PIN_NET:
                continue
            got = it.getNet().getName() if it.getNet() else None
            if got != PIN_NET[mt.getName()]:
                print(f"[FAIL] {inst.getName()}/{mt.getName()} on {got}, want {PIN_NET[mt.getName()]}")
                wrong_net += 1
            for mp in mt.getMPins():
                for g in mp.getGeometry():
                    y0, y1 = (g.yMin(), g.yMax()) if o == "R0" else (h - g.yMax(), h - g.yMin())
                    shapes[g.getTechLayer().getName()].append(
                        ((bb.xMin() + g.xMin(), bb.yMin() + y0, bb.xMin() + g.xMax(), bb.yMin() + y1),
                         PIN_NET[mt.getName()], f"{inst.getName()}/{mt.getName()}"))
    overlaps = 0
    for layer, lst in shapes.items():
        lst.sort(key=lambda s: s[0][0])
        active = []
        for s in lst:
            active = [a for a in active if a[0][2] > s[0][0]]
            for a in active:
                if a[1] != s[1] and a[0][1] < s[0][3] and s[0][1] < a[0][3]:
                    overlaps += 1
                    if overlaps <= 10:
                        r = lambda t: tuple(round(v / u, 3) for v in t)
                        print(f"[FAIL] {layer}: {a[1]} {a[2]} {r(a[0])} overlaps {s[1]} {s[2]} {r(s[0])}")
            active.append(s)
    print(f"rails: {overlaps} VPWR/VGND overlaps, {wrong_net} SRAM supply pins on the wrong net")
    fails += overlaps + wrong_net

    # 2. placement -------------------------------------------------------------
    stranded = 0
    for i in blk.getInsts():
        m = i.getMaster()
        if m.isBlock() or any(f in m.getName() for f in FILLERS):
            continue
        b = i.getBBox()
        for s in boxes:
            if b.xMax() > s.xMin() and b.xMin() < s.xMax() and b.yMin() < s.yMax():
                stranded += 1
                break
    print(f"placement: {stranded} standard cells below or level with an SRAM in its x range")
    fails += stranded

    # 3. routing -----------------------------------------------------------------
    if drc_path:
        over = power = 0
        kinds = collections.Counter()
        for v in open(drc_path).read().split("violation type: ")[1:]:
            m = re.search(r"\(([\d.]+), ([\d.]+)\) - \(([\d.]+), ([\d.]+)\) on Layer (\w+)", v)
            if not m:
                continue
            x, y = float(m.group(1)) * u, float(m.group(2)) * u
            if any(s.xMin() <= x <= s.xMax() and s.yMin() <= y <= s.yMax() for s in boxes):
                over += 1
                kinds[(v.split("\n")[0], m.group(5))] += 1
                if re.search(r"net:(VPWR|VGND)\b", v):
                    power += 1
        print(f"routing: {over} violations over SRAMs, {power} of them on VPWR/VGND")
        for (t, layer), n in kinds.most_common(5):
            print(f"    {n:6d} {t} on {layer}")
        fails += over

    print("RESULT:", "FAIL" if fails else "PASS")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main(*sys.argv[1:3]))
