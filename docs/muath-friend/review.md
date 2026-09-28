Hardening was not sucessful because of unresolved DRC violations (e.g. short & spacing). The allocated area is 6x4 which is ~24k logic cells, the synthesized design size is about ~31k logic cells – this is higher than what the allocation is offering.
Possible reasons of hardening failure:  
- The design is bigger than the available area, therefore the toolchain attempted squeezing the logic into the allocation which caused the DRC violation loop
- SRAM file not following the templates provided by TT (main culprit which uses 6k flip-flops)

Solution to the SRAM issue is to integrate the standard TT templates for SRAM, this will impose a decrease/increase in SRAM size as creating an unusual size might break hardening later.
-https://tinytapeout.com/specs/memory/