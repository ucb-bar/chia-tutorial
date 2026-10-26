/* Bulk tensor/frame staging: a working set larger than Rocket's L1 D-cache. */
#include "memory_test.h"

enum { BLOCKS = 16, WORDS = 2048, TOTAL = BLOCKS * WORDS, GUARDS = 8 };
static uint64_t source[TOTAL] ALIGNED;
static uint64_t destination[TOTAL + 2 * GUARDS] ALIGNED;

void initialise_benchmark(void)
{
    fill_pattern(source, ARRAY_WORDS(source));
    fill_guard(destination, ARRAY_WORDS(destination));
}

int benchmark(void)
{
    for (size_t block = 0; block < BLOCKS; ++block)
        memcpy(destination + GUARDS + block * WORDS, source + block * WORDS,
               WORDS * sizeof(uint64_t));
    return (int)checksum(destination, ARRAY_WORDS(destination));
}

int verify_benchmark(int result)
{
    if (!check_pattern(source, ARRAY_WORDS(source)))
        return 0;
    for (size_t i = 0; i < GUARDS; ++i)
        if (destination[i] != GUARD || destination[GUARDS + TOTAL + i] != GUARD)
            return 0;
    for (size_t i = 0; i < TOTAL; ++i)
        if (destination[i + GUARDS] != pattern(i))
            return 0;
    return result == 1810215813;
}
