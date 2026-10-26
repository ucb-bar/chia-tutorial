/* Tile placement: scatter a linear input into padded, permuted output tiles. */
#include "memory_test.h"

enum { BLOCKS = 128, WORDS = 128, STRIDE = WORDS + 8 };
static uint64_t source[BLOCKS * WORDS] ALIGNED;
static uint64_t destination[BLOCKS * STRIDE] ALIGNED;

void initialise_benchmark(void)
{
    fill_pattern(source, ARRAY_WORDS(source));
    fill_guard(destination, ARRAY_WORDS(destination));
}

int benchmark(void)
{
    for (size_t block = 0; block < BLOCKS; ++block) {
        size_t tile = (block * 37) & (BLOCKS - 1);
        memcpy(destination + tile * STRIDE + 4, source + block * WORDS,
               WORDS * sizeof(uint64_t));
    }
    return (int)checksum(destination, ARRAY_WORDS(destination));
}

int verify_benchmark(int result)
{
    if (!check_pattern(source, ARRAY_WORDS(source)))
        return 0;
    for (size_t tile = 0; tile < BLOCKS; ++tile)
        for (size_t word = 0; word < STRIDE; ++word) {
            /* 45 is the inverse of 37 modulo 128. */
            size_t block = (tile * 45) % BLOCKS;
            uint64_t expected = word >= 4 && word < WORDS + 4
                ? pattern(block * WORDS + word - 4) : GUARD;
            if (destination[tile * STRIDE + word] != expected)
                return 0;
        }
    return result == 30013262;
}
