/* Many short record copies, resembling container/crypto staging without compute. */
#include "memory_test.h"

enum { BLOCKS = 256, WORDS = 8, STRIDE = WORDS + 2 };
static uint64_t source[BLOCKS * WORDS] ALIGNED;
extern uint64_t destination[BLOCKS * STRIDE];

void initialise_benchmark(void)
{
    fill_pattern(source, ARRAY_WORDS(source));
    fill_guard(destination, ARRAY_WORDS(destination));
}

void benchmark(void)
{
    for (size_t block = 0; block < BLOCKS; ++block)
        memcpy(destination + block * STRIDE + 1, source + block * WORDS,
               WORDS * sizeof(uint64_t));
}
