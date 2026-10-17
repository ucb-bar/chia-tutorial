// RoCC memcpy accelerator harness.
//
// The loop replaces everything between the BEGIN / END markers inside
// memcpy_rocc() with the LLM's body; as committed, the body is the software
// baseline. Everything else is fixed: correctness tests, then timed
// evaluations, each checked by check_copy().
//
// Output (parsed by results.py, one line each):
//   PLAN tests=<n> evals=<m>
//   TEST <name> <PASS|FAIL> ret=<r> mismatches=<k>
//   EVAL <name> <PASS|FAIL> bytes=<n> cycles=<c> ret=<r> mismatches=<k>
//   DONE

#include <stdint.h>
#include <stddef.h>
#include <stdio.h>
#include <string.h>
#include "rocc.h"

#define read_csr(reg) ({ unsigned long __tmp; \
  asm volatile ("csrr %0, " #reg : "=r"(__tmp)); \
  __tmp; })

#define rdcycle() read_csr(cycle)

// Largest copy any test or eval makes, and the guard band on each side of it.
// A case's source and destination start PAD + offset (< PAD) bytes into their
// buffers; the extra PAD leaves room for the offset.
#define MAX_BYTES 16384
#define PAD 64
#define SENTINEL 0xA5

static uint8_t src_buf[MAX_BYTES + 3 * PAD] __attribute__((aligned(64)));
static uint8_t dst_buf[MAX_BYTES + 3 * PAD] __attribute__((aligned(64)));

// Copy len bytes from src to dst. Return 1 for success.
__attribute__((noinline))
int memcpy_rocc(const void *src, void *dst, int len) {
    // ===== memcpy_rocc body: BEGIN =====
    memcpy(dst, src, (size_t)len);
    return 1;
    // ===== memcpy_rocc body: END =====
}

static uint8_t pattern(int i, int seed) {
    uint8_t v = (uint8_t)(i * 7 + seed * 13 + 1);
    return v == SENTINEL ? 0x5A : v;
}

// The bytes a case can touch: the leading guard band, its offset, the copy,
// and a trailing guard band. prepare() and check_copy() stay inside it, so a
// short copy costs a short setup and check.
static int span(int len, int src_off, int dst_off) {
    return PAD + (src_off > dst_off ? src_off : dst_off) + len + PAD;
}

// Fill the case's span of the source buffer with a seed-dependent pattern and
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

struct copy_case {
    const char *name;
    int len;
    int src_off;   // byte offset from a 64-byte-aligned address
    int dst_off;
};

static const struct copy_case tests[] = {
    {"zero_bytes",           0,    0, 0},
    {"one_byte",             1,    0, 0},
    {"seven_bytes",          7,    0, 0},
    {"one_word",             8,    0, 0},
    {"one_word_src_unalign", 8,    3, 0},
    {"one_word_dst_unalign", 8,    0, 5},
    {"nine_bytes",           9,    0, 0},
    {"same_misalign",        24,   3, 3},
    {"diff_misalign",        61,   1, 6},
    {"cache_line",           64,   0, 0},
    {"cache_line_plus_one",  65,   0, 0},
    {"odd_large",            1021, 7, 2},
};

static const struct copy_case evals[] = {
    {"copy_256B",            256,   0, 0},
    {"copy_1KiB",            1024,  0, 0},
    {"copy_4KiB",            4096,  0, 0},
    {"copy_16KiB",           16384, 0, 0},
    {"copy_4KiB_unaligned",  4093,  3, 5},
};

#define NUM_TESTS (int)(sizeof(tests) / sizeof(tests[0]))
#define NUM_EVALS (int)(sizeof(evals) / sizeof(evals[0]))

int main(void) {
    printf("PLAN tests=%d evals=%d\n", NUM_TESTS, NUM_EVALS);

    for (int t = 0; t < NUM_TESTS; t++) {
        const struct copy_case *c = &tests[t];
        prepare(span(c->len, c->src_off, c->dst_off), t);
        int ret = memcpy_rocc(src_buf + PAD + c->src_off, dst_buf + PAD + c->dst_off, c->len);
        int mismatches = check_copy(c->len, c->src_off, c->dst_off, t);
        printf("TEST %s %s ret=%d mismatches=%d\n", c->name,
               (ret == 1 && mismatches == 0) ? "PASS" : "FAIL", ret, mismatches);
    }

    for (int e = 0; e < NUM_EVALS; e++) {
        const struct copy_case *c = &evals[e];
        int seed = 100 + e;
        prepare(span(c->len, c->src_off, c->dst_off), seed);
        unsigned long t0 = rdcycle();
        int ret = memcpy_rocc(src_buf + PAD + c->src_off, dst_buf + PAD + c->dst_off, c->len);
        unsigned long t1 = rdcycle();
        int mismatches = check_copy(c->len, c->src_off, c->dst_off, seed);
        printf("EVAL %s %s bytes=%d cycles=%lu ret=%d mismatches=%d\n", c->name,
               (ret == 1 && mismatches == 0) ? "PASS" : "FAIL", c->len, t1 - t0, ret, mismatches);
    }

    printf("DONE\n");
    return 0;
}
