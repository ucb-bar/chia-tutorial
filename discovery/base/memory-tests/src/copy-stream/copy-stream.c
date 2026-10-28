/* Bulk tensor/frame staging: a working set larger than Rocket's L1 D-cache. */
#include "memory_test.h"

enum { BLOCKS = 16, WORDS = 2048, TOTAL = BLOCKS * WORDS, GUARDS = 8 };
static uint64_t source[TOTAL] ALIGNED;
extern uint64_t destination[TOTAL + 2 * GUARDS];

void initialise_benchmark(void)
{
    fill_pattern(source, ARRAY_WORDS(source));
    fill_guard(destination, ARRAY_WORDS(destination));
}

void benchmark(void)
{
    for (size_t block = 0; block < BLOCKS; ++block)
        memcpy(destination + GUARDS + block * WORDS, source + block * WORDS,
               WORDS * sizeof(uint64_t));
}
