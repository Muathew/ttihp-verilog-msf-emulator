# SPDX-License-Identifier: Apache-2.0
"""Random differential verification: RTL vs the independent ISA model.

For every seed a constrained-random program (rand_prog.py) and a random pad
input schedule are generated and run through diffcheck.run_and_compare:
every issue / stall-release event (cycle, thread, PC, word) and the full
architectural state at each issue must match isa_model.IsaModel exactly.
A failure prints the seed, so it reproduces with MSF_SEED=<seed>.

Environment:
    MSF_SEEDS   seeds per test (default 40)
    MSF_SEED    run just this seed
    MSF_CYCLES  cycles per program (default 1500)
"""
import os
import random

import cocotb
from cocotb.clock import Clock

from diffcheck import Probe, run_and_compare
from rand_prog import Gen, input_schedule

CYCLES = int(os.environ.get("MSF_CYCLES", "1500"))


def _seeds(base, default):
    if "MSF_SEED" in os.environ:
        return [int(os.environ["MSF_SEED"])]
    n = int(os.environ.get("MSF_SEEDS", default))
    return [base + i for i in range(n)]


async def _run(dut, seeds, dual):
    cocotb.start_soon(Clock(dut.clk, 80, unit="ns").start())
    dut.ena.value = 1
    probe = Probe(dut)
    tot = [0, 0, 0]
    for s in seeds:
        src = Gen(s, dual=dual).program()
        rng = random.Random(s)
        data = {a: rng.randrange(65536) for a in range(128)}
        uio, ui = input_schedule(s, CYCLES)
        res = await run_and_compare(dut, src, CYCLES, uio, ui, data, probe,
                                    tag=f"seed {s} ({'dual' if dual else 'single'})")
        tot = [a + b for a, b in zip(tot, res)]
    dut._log.info(f"{len(seeds)} programs: {tot[0]} instructions ({tot[1]} on T1), "
                  f"{tot[2]} stall releases -- RTL == model")


@cocotb.test()
async def test_random_single_thread(dut):
    await _run(dut, _seeds(1000, 40), dual=False)


@cocotb.test()
async def test_random_dual_thread(dut):
    await _run(dut, _seeds(5000, 40), dual=True)
