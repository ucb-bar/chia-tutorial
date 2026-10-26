/* Many short record copies, resembling container/crypto staging without compute. */
#include "memory_test.h"

enum { BLOCKS = 2048, WORDS = 8, STRIDE = WORDS + 2 };
static uint64_t source[BLOCKS * WORDS] ALIGNED;
static uint64_t destination[BLOCKS * STRIDE] ALIGNED;

void initialise_benchmark(void)
{
    fill_pattern(source, ARRAY_WORDS(source));
    fill_guard(destination, ARRAY_WORDS(destination));
}

int benchmark(void)
{
    for (size_t block = 0; block < BLOCKS; ++block)
        memcpy(destination + block * STRIDE + 1, source + block * WORDS,
               WORDS * sizeof(uint64_t));
    return (int)checksum(destination, ARRAY_WORDS(destination));
}

int verify_benchmark(int result)
{
    if (!check_pattern(source, ARRAY_WORDS(source)))
        return 0;
    for (size_t block = 0; block < BLOCKS; ++block) {
        if (destination[block * STRIDE] != GUARD ||
            destination[block * STRIDE + WORDS + 1] != GUARD)
            return 0;
        for (size_t word = 0; word < WORDS; ++word)
            if (destination[block * STRIDE + 1 + word] != pattern(block * WORDS + word))
                return 0;
    }
    return result == 1142204022;
}
