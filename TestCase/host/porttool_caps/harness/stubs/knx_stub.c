/*
 * The KNX session entry points, stubbed.
 *
 * The real ones live in knx_test.c behind two timing ISRs and a TIM1 input
 * capture; none of that exists on a host. What the contract test decides is
 * the session's behaviour above that line: that listen mode never transmits,
 * that a character coming back closes the loop, that a wrong byte is counted
 * as a mismatch rather than a pass, and that the frame says which.
 *
 * The loopback here is one character deep, which is all a tick uses.
 */

#include "KNX/knx_test.h"

#include <stdio.h>

int test_knx_init_count;
int test_knx_sent;              /* how many characters the session put out */
uint8_t test_knx_last_sent;

/* Set to 0 for a bus that swallows everything, or corrupt to model a byte that
 * comes back wrong - the two failures a KNX session has to tell apart. */
int test_knx_loopback = 1;
int test_knx_corrupt;
int test_knx_framing_bad;

int test_knx_bus = 0;           /* 0 ok, 1 dead, 2 odd */
uint32_t test_knx_pulses;

static int      q_full;
static uint8_t  q_byte;

int KNX_Test_SessionInit(void)
{
    test_knx_init_count++;
    printf("KNX_TEST: capture and bit engine started (stub)\r\n");
    q_full = 0;
    return 1;
}

void KNX_Test_SessionStatsReset(void)
{
    q_full = 0;
    test_knx_pulses = 0;
}

int KNX_Test_SessionSendChar(uint8_t b)
{
    test_knx_sent++;
    test_knx_last_sent = b;
    test_knx_pulses += 4u;      /* a character is a handful of active pulses */
    if (test_knx_loopback) {
        q_byte = test_knx_corrupt ? (uint8_t)(b ^ 0xFFu) : b;
        q_full = 1;
    }
    return 1;
}

int KNX_Test_SessionPollChar(uint8_t *out, uint8_t *framing_ok)
{
    if (!q_full) {
        return 0;
    }
    q_full = 0;
    if (out != NULL)        { *out = q_byte; }
    if (framing_ok != NULL) { *framing_ok = (uint8_t)(test_knx_framing_bad ? 0 : 1); }
    return 1;
}

void KNX_Test_SessionStats(knx_session_stats_t *out)
{
    if (out == NULL) {
        return;
    }
    out->bus     = (uint8_t)test_knx_bus;
    out->vcc_ok  = (uint8_t)((test_knx_bus == 0) ? 1 : 0);
    out->bus_ok  = (uint8_t)((test_knx_bus == 0) ? 1 : 0);
    out->rx_idle = (uint8_t)((test_knx_bus == 1) ? 1 : 0);

    out->pulses  = test_knx_pulses;
    out->dropped = 0;

    out->w_min = 33; out->w_max = 37; out->w_avg = 35; out->w_count = test_knx_pulses;
    out->d_min = 5;  out->d_max = 9;  out->d_avg = 7;  out->d_count = test_knx_pulses;

    out->ms_since_edge = 12;
}
