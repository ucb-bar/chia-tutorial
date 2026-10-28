// RoCC memcpy accelerator harness.

#include <stdint.h>
#include <stddef.h>
#include <stdio.h>
#include <string.h>
#include "rocc.h"

#ifndef CASE_LEN
#error "compile with a case's -D flags (results.py, Case.cflags)"
#endif

#define CASE_STR_(x) #x
#define CASE_STR(x) CASE_STR_(x)

#define read_csr(reg) ({ unsigned long __tmp; \
  asm volatile ("csrr %0, " #reg : "=r"(__tmp)); \
  __tmp; })

#define rdcycle() read_csr(cycle)

// The guard band on each side of the copy. The source and destination start
// PAD + offset (< PAD) bytes into their buffers; the extra PAD leaves room for
// the offset.
#define PAD 64
#define SENTINEL 0xA5

static uint8_t src_buf[CASE_LEN + 3 * PAD] __attribute__((aligned(64)));
static uint8_t dst_buf[CASE_LEN + 3 * PAD] __attribute__((aligned(64)));

// Copy len bytes from src to dst. Return 1 for success.
__attribute__((noinline))
int memcpy_rocc(const void *src, void *dst, int len) {
    // ===== memcpy_rocc body: BEGIN =====
    uintptr_t ret;
    ROCC_INSTRUCTION_DSS(0, ret, (uintptr_t)src,
                         ((uintptr_t)(uint32_t)(uintptr_t)dst) | (((uintptr_t)(uint32_t)len) << 32), 0);
    return (int)ret;
    // ===== memcpy_rocc body: END =====
}

static uint8_t pattern(int i, int seed) {
    uint8_t v = (uint8_t)(i * 7 + seed * 13 + 1);
    return v == SENTINEL ? 0x5A : v;
}

// The bytes the case can touch: the leading guard band, its offset, the copy,
// and a trailing guard band. prepare() and check_copy() stay inside it.
static int span(int len, int src_off, int dst_off) {
    return PAD + (src_off > dst_off ? src_off : dst_off) + len + PAD;
}

// Fill the span of the source buffer with a seed-dependent pattern and
// of the destination buffer with SENTINEL.
static void prepare(int n, int seed) {
    for (int i = 0; i < n; i++) {
        src_buf[i] = pattern(i, seed);
        dst_buf[i] = SENTINEL;
    }
    asm volatile ("fence" ::: "memory");
}

// Compare src and dst over the case's span with byte loads. Counts as a
// mismatch: a destination byte that differs from its source byte, a byte
// outside the destination range (in the guard bands) that is no longer
// SENTINEL, or a source byte that changed.
static int check_copy(int len, int src_off, int dst_off, int seed) {
    volatile uint8_t *s = src_buf;
    volatile uint8_t *d = dst_buf;
    int mismatches = 0;
    asm volatile ("fence" ::: "memory");
    for (int i = 0; i < span(len, src_off, dst_off); i++) {
        if (s[i] != pattern(i, seed))
            mismatches++;
        int j = i - (PAD + dst_off);   // index into the copy, if inside it
        if (j >= 0 && j < len) {
            if (d[i] != s[PAD + src_off + j])
                mismatches++;
        } else if (d[i] != SENTINEL) {
            mismatches++;
        }
    }
    return mismatches;
}

int main(void) {
    int len = CASE_LEN, src_off = CASE_SRC_OFF, dst_off = CASE_DST_OFF;
    prepare(span(len, src_off, dst_off), CASE_SEED);
    unsigned long t0 = rdcycle();
    int ret = memcpy_rocc(src_buf + PAD + src_off, dst_buf + PAD + dst_off, len);
    unsigned long t1 = rdcycle();
    int mismatches = check_copy(len, src_off, dst_off, CASE_SEED);
    const char *status = (ret == 1 && mismatches == 0) ? "PASS" : "FAIL";
#if CASE_IS_EVAL
    printf("EVAL %s %s bytes=%d cycles=%lu ret=%d mismatches=%d\n",
           CASE_STR(CASE_NAME), status, len, t1 - t0, ret, mismatches);
#else
    (void)t0; (void)t1;
    printf("TEST %s %s ret=%d mismatches=%d\n", CASE_STR(CASE_NAME), status, ret, mismatches);
#endif
    printf("DONE\n");
    return 0;
}
