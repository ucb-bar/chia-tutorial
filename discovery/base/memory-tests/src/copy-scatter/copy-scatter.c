/* Tile placement: scatter a linear input into padded, permuted output tiles. */
#include "memory_test.h"

enum { BLOCKS = 32, WORDS = 128, STRIDE = WORDS + 8 };
static uint64_t source[BLOCKS * WORDS] ALIGNED;
extern uint64_t destination[BLOCKS * STRIDE];

void initialise_benchmark(void)
{
    fill_pattern(source, ARRAY_WORDS(source));
    fill_guard(destination, ARRAY_WORDS(destination));
}

void benchmark(void)
{
    for (size_t block = 0; block < BLOCKS; ++block) {
        size_t tile = (block * 37) & (BLOCKS - 1);
        memcpy(destination + tile * STRIDE + 4, source + block * WORDS,
               WORDS * sizeof(uint64_t));
    }
}
