# SPDX-License-Identifier: Apache-2.0
"""LibreLane plugin: move the Metal4 PDN stripes onto the SRAM supply columns.

On IHP CMOS5L a Tiny Tapeout block has one stripe layer, Metal4, which is
also the layer of the SRAM macro's power pins. pdngen cuts every stripe short
of a fixed macro, which leaves the macro unpowered and the cut stripes as
half-height power ports (the precheck wants every port to reach both the top
and bottom edge). `Project.ExtendPowerStripes` runs `odb_sram_stripes.py`
after `OpenROAD.GeneratePDN`: it removes the stripes that cross each macro and
draws full-height stripes on the macro's own supply columns instead.

LibreLane imports every module on the Python path whose name starts with
``librelane_plugin_``. The Tiny Tapeout GDS action runs LibreLane from the
repository root, so this file is found without installing anything.
`src/config.json` adds the step with

    "meta": {"substituting_steps": {"+OpenROAD.GeneratePDN": "Project.ExtendPowerStripes"}}

The approach and the netgen fix below come from MarcosAsh/protocol-emulator
(librelane_plugin_sram_pdn.py, odb_sram_stripes.py), which follows
kdp1965/ihp-um-janestreet-prism (odb_stripes.py); both are Apache-2.0.
"""
import json as _json
import os
import re as _re
import types as _types

from librelane.steps import Step
from librelane.steps.odb import OdbpyStep

import librelane.steps.netgen as _netgen

HERE = os.path.dirname(os.path.abspath(__file__))


@Step.factory.register()
class ExtendPowerStripes(OdbpyStep):
    id = "Project.ExtendPowerStripes"
    name = "Extend Power Stripes Over SRAM"

    def get_script_path(self):
        return os.path.join(HERE, "odb_sram_stripes.py")


# netgen writes the SRAM's power pin names (VDD!, VSS!, VDDARRAY!) into its
# LVS JSON with a stray backslash ("\VDD!"), which is not a valid JSON escape,
# so librelane.steps.netgen.LVS dies in json.loads before the LVS checker
# runs. Repair such escapes and retry.
_BAD_ESCAPE = _re.compile(r'\\(\\|[^"\\/bfnrtu])')


def _repair_escapes(s):
    return _BAD_ESCAPE.sub(
        lambda m: "\\\\" if m.group(1) == "\\" else "\\\\" + m.group(1), s
    )


def _loads_repairing_escapes(s, *args, **kwargs):
    try:
        return _json.loads(s, *args, **kwargs)
    except _json.JSONDecodeError:
        return _json.loads(_repair_escapes(s), *args, **kwargs)


_netgen.json = _types.SimpleNamespace(
    loads=_loads_repairing_escapes,
    load=_json.load,
    dumps=_json.dumps,
    dump=_json.dump,
    JSONDecodeError=_json.JSONDecodeError,
)
