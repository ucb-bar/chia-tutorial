/* Packet assembly: gather permuted records into one contiguous output. */
#include "memory_test.h"

enum { BLOCKS = 256, WORDS = 64, STRIDE = WORDS + 2 };
static uint64_t source[BLOCKS * STRIDE] ALIGNED;
extern uint64_t destination[BLOCKS * WORDS + 2];

void initialise_benchmark(void)
{
    fill_pattern(source, ARRAY_WORDS(source));
    fill_guard(destination, ARRAY_WORDS(destination));
}

void benchmark(void)
{
    for (size_t block = 0; block < BLOCKS; ++block) {
        size_t record = (block * 73) & (BLOCKS - 1);
        memcpy(destination + 1 + block * WORDS, source + record * STRIDE + 1,
               WORDS * sizeof(uint64_t));
    }
}
