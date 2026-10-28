#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Assemble the HIL test firmware into msf_images.h (default: src/msf_images.h).

The images are the firmware library demos (firmware/*.s, assembled exactly as
test/test_firmware.py does) plus the HIL helpers in firmware/ next to this
script. Re-run it whenever firmware/ or test/assembler.py changes.
"""
import argparse
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
sys.path.insert(0, str(REPO / "test"))
from assembler import assemble_file  # noqa: E402

# Scaled link-pulse interval, as test_fw_10baset_frame_fcs_tpidl_nlp uses.
ETH_NLP = {"NLP_CHUNK": 3000, "NLP_CHUNKS": 2}

IMAGES = [
    ("uart_tx", REPO / "firmware" / "uart_tx.s", {}),
    ("spi_master", REPO / "firmware" / "spi_master.s", {}),
    ("i2c_master", REPO / "firmware" / "i2c_master.s", {}),
    ("usb_ls_tx", REPO / "firmware" / "usb_ls_tx.s", {}),
    ("usb_ls_rx", REPO / "firmware" / "usb_ls_rx.s", {}),
    ("eth_10baset_tx", REPO / "firmware" / "eth_10baset_tx.s", ETH_NLP),
    ("thread_demo", REPO / "firmware" / "thread_demo.s", {}),
    ("jtag", REPO / "firmware" / "jtag.s", {}),
    ("swd", REPO / "firmware" / "swd.s", {}),
    ("onewire", REPO / "firmware" / "onewire.s", {}),
    ("spi_target_m0", REPO / "firmware" / "spi_target.s", {"MODE": 0}),
    ("spi_target_m3", REPO / "firmware" / "spi_target.s", {"MODE": 3}),
    ("spi_flash_m0", REPO / "firmware" / "spi_flash.s", {"MODE": 0}),
    ("spi_flash_m3", REPO / "firmware" / "spi_flash.s", {"MODE": 3}),
    ("i2c_target", REPO / "firmware" / "i2c_target.s", {}),
    ("i2c_target_stretch", REPO / "firmware" / "i2c_target.s", {"STRETCH": 1}),
    ("mon_uart", REPO / "firmware" / "mon_uart.s", {}),
    ("mon_i2c", REPO / "firmware" / "mon_i2c.s", {}),
    ("mon_spi_m0", REPO / "firmware" / "mon_spi.s", {"MODE": 0}),
    ("mon_spi_m1", REPO / "firmware" / "mon_spi.s", {"MODE": 1}),
    ("mon_spi_m2", REPO / "firmware" / "mon_spi.s", {"MODE": 2}),
    ("mon_spi_m3", REPO / "firmware" / "mon_spi.s", {"MODE": 3}),
    ("bridge_m0", REPO / "firmware" / "bridge.s", {"MODE": 0}),
    ("bridge_m1", REPO / "firmware" / "bridge.s", {"MODE": 1}),
    ("bridge_m2", REPO / "firmware" / "bridge.s", {"MODE": 2}),
    ("bridge_m3", REPO / "firmware" / "bridge.s", {"MODE": 3}),
    ("logic_analyzer", REPO / "firmware" / "logic_analyzer.s",
     {"DIV": 2, "MASK": 0xF0, "VAL": 0x30, "EDGE": 0x1000, "N": 4, "BASE": 512, "POST": 9,
      "BIT": 12}),
    ("ps2_q240", REPO / "firmware" / "ps2_device.s", {"Q": 240}),
    ("ps2_q60", REPO / "firmware" / "ps2_device.s", {"Q": 60}),
    ("can", REPO / "firmware" / "can.s", {}),
    ("uart_b104_s104", REPO / "firmware" / "uart_tx.s", {"BIT": 104, "STOP": 104}),
    ("uart_b99_s104", REPO / "firmware" / "uart_tx.s", {"BIT": 99, "STOP": 104}),
    ("uart_b110_s37", REPO / "firmware" / "uart_tx.s", {"BIT": 110, "STOP": 37}),
    ("uart_b104_s300", REPO / "firmware" / "uart_tx.s", {"BIT": 104, "STOP": 300}),
    ("uart_b94_s94", REPO / "firmware" / "uart_tx.s", {"BIT": 94, "STOP": 94}),
    ("uart_b95_s95", REPO / "firmware" / "uart_tx.s", {"BIT": 95, "STOP": 95}),
    ("uart_b96_s96", REPO / "firmware" / "uart_tx.s", {"BIT": 96, "STOP": 96}),
    ("uart_b97_s97", REPO / "firmware" / "uart_tx.s", {"BIT": 97, "STOP": 97}),
    ("uart_b98_s98", REPO / "firmware" / "uart_tx.s", {"BIT": 98, "STOP": 98}),
    ("uart_b99_s99", REPO / "firmware" / "uart_tx.s", {"BIT": 99, "STOP": 99}),
    ("uart_b100_s100", REPO / "firmware" / "uart_tx.s", {"BIT": 100, "STOP": 100}),
    ("uart_b101_s101", REPO / "firmware" / "uart_tx.s", {"BIT": 101, "STOP": 101}),
    ("uart_b102_s102", REPO / "firmware" / "uart_tx.s", {"BIT": 102, "STOP": 102}),
    ("uart_b103_s103", REPO / "firmware" / "uart_tx.s", {"BIT": 103, "STOP": 103}),
    ("uart_b105_s105", REPO / "firmware" / "uart_tx.s", {"BIT": 105, "STOP": 105}),
    ("uart_b106_s106", REPO / "firmware" / "uart_tx.s", {"BIT": 106, "STOP": 106}),
    ("uart_b107_s107", REPO / "firmware" / "uart_tx.s", {"BIT": 107, "STOP": 107}),
    ("uart_b108_s108", REPO / "firmware" / "uart_tx.s", {"BIT": 108, "STOP": 108}),
    ("uart_b109_s109", REPO / "firmware" / "uart_tx.s", {"BIT": 109, "STOP": 109}),
    ("uart_b110_s110", REPO / "firmware" / "uart_tx.s", {"BIT": 110, "STOP": 110}),
    ("uart_b111_s111", REPO / "firmware" / "uart_tx.s", {"BIT": 111, "STOP": 111}),
    ("uart_b112_s112", REPO / "firmware" / "uart_tx.s", {"BIT": 112, "STOP": 112}),
    ("uart_b113_s113", REPO / "firmware" / "uart_tx.s", {"BIT": 113, "STOP": 113}),
    ("uart_b114_s114", REPO / "firmware" / "uart_tx.s", {"BIT": 114, "STOP": 114}),
    ("i2c_ql9_qh9", REPO / "firmware" / "i2c_master.s", {"QL": 9, "QH": 9}),
    ("i2c_ql9_qh3", REPO / "firmware" / "i2c_master.s", {"QL": 9, "QH": 3}),
    ("i2c_ql2_qh9", REPO / "firmware" / "i2c_master.s", {"QL": 2, "QH": 9}),
    ("i2c_ql5_qh14", REPO / "firmware" / "i2c_master.s", {"QL": 5, "QH": 14}),
    ("i2c_ql1_qh1", REPO / "firmware" / "i2c_master.s", {"QL": 1, "QH": 1}),
    ("bridge_sh1_sl1", REPO / "firmware" / "bridge.s", {"SH": 1, "SL": 1}),
    ("bridge_sh3_sl1", REPO / "firmware" / "bridge.s", {"SH": 3, "SL": 1}),
    ("bridge_sh1_sl5", REPO / "firmware" / "bridge.s", {"SH": 1, "SL": 5}),
    ("bridge_sh10_sl2", REPO / "firmware" / "bridge.s", {"SH": 10, "SL": 2}),
    ("boot_probe", HERE / "firmware" / "boot_probe.s", {}),
    ("four_threads", HERE / "firmware" / "four_threads.s", {}),
    ("dump_ram", HERE / "firmware" / "dump_ram.s", {}),
]
BASE = 128


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("-o", "--output", type=Path, default=HERE / "src" / "msf_images.h")
    args = ap.parse_args()

    lines = [
        "// Generated by fpga/ps/hil/gen_images.py; do not edit.",
        "#ifndef MSF_IMAGES_H",
        "#define MSF_IMAGES_H",
        "",
        "#include <stdint.h>",
        "",
        "typedef struct {",
        "    const char *name;",
        "    uint16_t addr;",
        "    uint16_t count;",
        "    const uint16_t *words;",
        "} msf_image;",
        "",
        f"#define MSF_ETH_NLP_CHUNK {ETH_NLP['NLP_CHUNK']}U",
        f"#define MSF_ETH_NLP_CHUNKS {ETH_NLP['NLP_CHUNKS']}U",
    ]
    for name, src, defines in IMAGES:
        words = assemble_file(str(src), base_addr=BASE, defines=defines)
        if BASE + len(words) > 1024:
            ap.error(f"{src.name}: {len(words)} words do not fit above {BASE}")
        body = ",\n".join(
            "    " + ", ".join(f"0x{w:04x}U" for w in words[i:i + 8])
            for i in range(0, len(words), 8))
        rel = src.relative_to(REPO).as_posix()
        lines += [
            "",
            f"/* {rel}{' ' + str(defines) if defines else ''} */",
            f"static const uint16_t {name}_words[{len(words)}] = {{",
            body,
            "};",
            f'static const msf_image img_{name} = {{"{name}", {BASE}U, {len(words)}U, '
            f"{name}_words}};",
        ]
    lines += ["", "#endif", ""]
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text("\n".join(lines), encoding="ascii")
    print(f"Wrote {len(IMAGES)} images to {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
