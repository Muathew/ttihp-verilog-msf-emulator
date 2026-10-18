// SPDX-License-Identifier: Apache-2.0
// Host HAL that runs the PS test code against the RTL in cocotb
// (fpga/tests/test_ps_hil.py). The test code runs in its own thread and each
// EMIO access blocks until the simulator has advanced the PL clock, so the C
// code and the RTL move in lockstep: an access takes `cycles` PL clocks,
// like a PS GPIO register access on the board.
#include <pthread.h>
#include <semaphore.h>
#include <string.h>

#include "msf_emio.h"
#include "msf_tests.h"

#ifdef _WIN32
#define MSF_EXPORT __declspec(dllexport)
#else
#define MSF_EXPORT __attribute__((visibility("default")))
#endif

#define PL_CLOCKS_PER_US 12U   /* pl_clk0 = 12 MHz */

enum { REQ_READ, REQ_WRITE, REQ_DELAY, REQ_EXIT };

static sem_t to_test, to_sim;
static pthread_t worker;
static int req, result, started;
static uint32_t req_value, read_value, emio_o, emio_t, elapsed, access_cycles;
static char only[64];

static void transact(int kind, uint32_t value)
{
    req = kind;
    req_value = value;
    sem_post(&to_sim);
    sem_wait(&to_test);
}

int msf_emio_init(void)
{
    transact(REQ_WRITE, 0U);
    return 0;
}

void msf_emio_write(uint32_t value)
{
    transact(REQ_WRITE, value & MSF_O_MASK);
}

uint32_t msf_emio_read(void)
{
    transact(REQ_READ, 0U);
    return read_value;
}

void msf_emio_delay_us(uint32_t us)
{
    transact(REQ_DELAY, us * PL_CLOCKS_PER_US);
}

static void *run_tests(void *arg)
{
    (void)arg;
    sem_wait(&to_test);
    result = msf_tests_run(only[0] ? only : 0);
    req = REQ_EXIT;
    sem_post(&to_sim);
    return 0;
}

MSF_EXPORT int msf_sim_count(void)
{
    return msf_tests_count();
}

MSF_EXPORT const char *msf_sim_name(int i)
{
    return msf_tests_name(i);
}

/* Start the test code (one test by name, or all for an empty name). */
MSF_EXPORT int msf_sim_start(const char *name, unsigned cycles)
{
    strncpy(only, name ? name : "", sizeof only - 1U);
    only[sizeof only - 1U] = '\0';
    access_cycles = cycles ? cycles : 1U;
    emio_o = 0U;
    emio_t = 0xffffffffU;              /* PS GPIO comes out of reset as inputs */
    elapsed = 0U;
    started = 0;
    result = -1;
    req = REQ_READ;
    sem_init(&to_test, 0, 0);
    sem_init(&to_sim, 0, 0);
    return pthread_create(&worker, 0, run_tests, 0);
}

/* One PL clock: emio_i = ps_gpio_i now; *o / *t = what the PS drives next.
 * Returns 1 once the test code has finished. */
MSF_EXPORT int msf_sim_cycle(uint32_t emio_i, uint32_t *o, uint32_t *t)
{
    if (!started) {                    /* run the test code up to its first access */
        started = 1;
        sem_post(&to_test);
        sem_wait(&to_sim);
    }
    if (req != REQ_EXIT) {
        uint32_t span = req == REQ_DELAY ? req_value : access_cycles;

        if (elapsed++ == 0U && req == REQ_WRITE) {
            emio_o = req_value;
            emio_t = ~MSF_O_MASK;
        }
        if (elapsed >= span) {          /* the access completes in this clock */
            if (req == REQ_READ)
                read_value = emio_i;
            elapsed = 0U;
            sem_post(&to_test);
            sem_wait(&to_sim);
        }
    }
    *o = emio_o;
    *t = emio_t;
    return req == REQ_EXIT;
}

/* Join the test thread; returns msf_tests_run()'s result (failures). */
MSF_EXPORT int msf_sim_finish(void)
{
    pthread_join(worker, 0);
    sem_destroy(&to_test);
    sem_destroy(&to_sim);
    return result;
}
