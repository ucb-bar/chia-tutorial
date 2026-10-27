/* Bare-metal adapter for pinned glibc memcpy vectors (see benchmarks/ADAPTATION.txt).
 * Validation and timing remain outside the model-editable function body. */
#include <stdint.h>
#include <stddef.h>
#include <stdio.h>
#include <string.h>
#ifndef SCALE_HOST_TEST
#include "rocc.h"
#define FENCE() asm volatile ("fence" ::: "memory")
static unsigned long cycle(void) { unsigned long x; asm volatile("rdcycle %0" : "=r"(x) :: "memory"); return x; }
#else
/* Host correctness tests ONLY; these values are NOT hardware measurements. */
#define FENCE() asm volatile ("" ::: "memory")
static unsigned long cycle(void) { static unsigned long x; return ++x; }
#endif

__attribute__((noinline)) int memcpy_rocc(const void *src, void *dst, int len) {
    // ===== memcpy_rocc body: BEGIN =====
    memcpy(dst, src, (size_t)len);
    return 1;
    // ===== memcpy_rocc body: END =====
}

typedef struct { const char *name; int eval, len, src, dst, reverse; } Case;
static const Case cases[] = {
/* CASE_TABLE */
};
#define PAD 4096
#define GUARD 64
#define STRIDE (((/* MAX_BYTES */ + 3 * PAD) + PAD - 1) / PAD * PAD)
static uint8_t arena[2 * STRIDE] __attribute__((aligned(PAD)));
static uint8_t pattern(int i) { return (uint8_t)(1 + 23 * i); }

int main(void) {
    int failed = 0;
    for (unsigned k = 0; k < sizeof(cases) / sizeof(cases[0]); ++k) {
        const Case *c = &cases[k];
        uint8_t *s = arena + (c->reverse ? STRIDE : 0) + PAD + c->src - GUARD;
        uint8_t *d = arena + (c->reverse ? 0 : STRIDE) + PAD + c->dst - GUARD;
        int span = c->len + 2 * GUARD;
        unsigned long elapsed = 0;
        int mismatches = 0, ret = 1;
        int warmups = c->eval ? /* WARMUPS */ : 0;
        int repeats = c->eval ? /* REPEATS */ : 1;
        for (int n = -warmups; n < repeats; ++n) {
            for (int i = 0; i < span; ++i) { s[i] = pattern(i - GUARD); d[i] = 0xA5; }
            FENCE();
            unsigned long start = cycle();
            int value = memcpy_rocc(s + GUARD, d + GUARD, c->len);
            FENCE();
            unsigned long stop = cycle();
            FENCE();
            if (n >= 0) elapsed += stop - start;
            if (value != 1) ret = value;
            /* Volatile byte reads: avoid trusting the memcpy being optimized. */
            volatile uint8_t *sv = s, *dv = d;
            for (int i = 0; i < span; ++i) {
                if (sv[i] != pattern(i - GUARD)) ++mismatches;
                int j = i - GUARD;
                uint8_t want = (j >= 0 && j < c->len) ? pattern(j) : 0xA5;
                if (dv[i] != want) ++mismatches;
            }
        }
        unsigned long measured = elapsed / repeats;
        int ok = (ret == 1 && mismatches == 0);
        printf("%s %s %s bytes=%d cycles=%lu ret=%d mismatches=%d\n",
               c->eval ? "EVAL" : "TEST", c->name, ok ? "PASS" : "FAIL",
               c->len, measured, ret, mismatches);
        failed |= !ok;
    }
    puts("DONE");
    return failed;
}
