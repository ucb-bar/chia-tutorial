/* Packet assembly: gather permuted records into one contiguous output. */
#include "memory_test.h"

enum { BLOCKS = 256, WORDS = 64, STRIDE = WORDS + 2 };
static uint64_t source[BLOCKS * STRIDE] ALIGNED;
static uint64_t destination[BLOCKS * WORDS + 2] ALIGNED;

void initialise_benchmark(void)
{
    fill_pattern(source, ARRAY_WORDS(source));
    fill_guard(destination, ARRAY_WORDS(destination));
}

int benchmark(void)
{
    for (size_t block = 0; block < BLOCKS; ++block) {
        size_t record = (block * 73) & (BLOCKS - 1);
        memcpy(destination + 1 + block * WORDS, source + record * STRIDE + 1,
               WORDS * sizeof(uint64_t));
    }
    return (int)checksum(destination, ARRAY_WORDS(destination));
}

int verify_benchmark(int result)
{
    if (!check_pattern(source, ARRAY_WORDS(source)) || destination[0] != GUARD ||
        destination[BLOCKS * WORDS + 1] != GUARD)
        return 0;
    for (size_t block = 0; block < BLOCKS; ++block)
        for (size_t word = 0; word < WORDS; ++word) {
            size_t index = ((block * 73) % BLOCKS) * STRIDE + 1 + word;
            if (destination[1 + block * WORDS + word] != pattern(index))
                return 0;
        }
    return result == 867411657;
}
