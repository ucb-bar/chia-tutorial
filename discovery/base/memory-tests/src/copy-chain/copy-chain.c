/* Dependent double-buffer pipeline; each copy consumes the previous output. */
#include "memory_test.h"

enum { STEPS = 512, WORDS = 64, STRIDE = WORDS + 2 };
extern uint64_t buffers[2 * STRIDE];
extern uint32_t witness;

void initialise_benchmark(void)
{
    fill_guard(buffers, ARRAY_WORDS(buffers));
    fill_pattern(buffers + 1, WORDS);
    witness = 0;
}

void benchmark(void)
{
    for (size_t step = 0; step < STEPS; ++step) {
        uint64_t *source = buffers + (step % 2) * STRIDE + 1;
        uint64_t *destination = buffers + ((step + 1) % 2) * STRIDE + 1;
        source[0] += step + 1;
        memcpy(destination, source, WORDS * sizeof(uint64_t));
        /* Observe every intermediate copy with very little scalar work. */
        witness = fold(witness, destination[0] ^ destination[WORDS - 1]);
    }
}
