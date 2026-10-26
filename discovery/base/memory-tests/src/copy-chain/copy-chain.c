/* Dependent double-buffer pipeline; each copy consumes the previous output. */
#include "memory_test.h"

enum { STEPS = 128, WORDS = 128, STRIDE = WORDS + 2 };
static uint64_t buffers[2 * STRIDE] ALIGNED;
static uint32_t witness;
static int guards_intact;

void initialise_benchmark(void)
{
    fill_guard(buffers, ARRAY_WORDS(buffers));
    fill_pattern(buffers + 1, WORDS);
    witness = 0;
    guards_intact = 1;
}

int benchmark(void)
{
    for (size_t step = 0; step < STEPS; ++step) {
        uint64_t *source = buffers + (step % 2) * STRIDE + 1;
        uint64_t *destination = buffers + ((step + 1) % 2) * STRIDE + 1;
        source[0] += step + 1;
        memcpy(destination, source, WORDS * sizeof(uint64_t));
        /* Latch transient overruns before a later copy can hide them. */
        if (destination[-1] != GUARD || destination[WORDS] != GUARD)
            guards_intact = 0;
        /* Observe every intermediate copy with very little scalar work. */
        witness = fold(witness, destination[0] ^ destination[WORDS - 1]);
    }
    return (int)((checksum(buffers, ARRAY_WORDS(buffers)) ^ witness) & UINT32_C(0x7fffffff));
}

int verify_benchmark(int result)
{
    uint32_t expected_witness = 0;
    for (size_t step = 0; step < STEPS; ++step) {
        uint64_t first = pattern(0) + (step + 1) * (step + 2) / 2;
        expected_witness = fold(expected_witness, first ^ pattern(WORDS - 1));
    }
    if (!guards_intact || witness != expected_witness)
        return 0;
    for (size_t buffer = 0; buffer < 2; ++buffer) {
        if (buffers[buffer * STRIDE] != GUARD || buffers[(buffer + 1) * STRIDE - 1] != GUARD)
            return 0;
        for (size_t word = 0; word < WORDS; ++word) {
            uint64_t expected = pattern(word) + (word == 0 ? STEPS * (STEPS + 1) / 2 : 0);
            if (buffers[buffer * STRIDE + 1 + word] != expected)
                return 0;
        }
    }
    return result == 1613899491;
}
