// SPDX-License-Identifier: Apache-2.0
// Hardware-in-the-loop protocol tests for the FPGA msfcore (msf_tests.c).
#ifndef MSF_TESTS_H
#define MSF_TESTS_H

int         msf_tests_count(void);
const char *msf_tests_name(int i);
/* Run every test (only == 0) or the one named `only`. Returns the number of
 * failures, or -1 if nothing ran. Leaves the core in reset, pins released. */
int         msf_tests_run(const char *only);

#endif
