/* Variable-size message staging, including zero length, tails and fallback. */
#include "memory_test.h"

enum { CASES = 24, REPEATS = 2, DEST_WORDS = 5288 };
static const size_t lengths[CASES] = {
    0, 7, 8, 31, 63, 64, 65, 127, 128, 129, 255, 256,
    257, 511, 512, 513, 1023, 1024, 1025, 2047, 2048, 2049, 4095, 4096
};
static const size_t offsets[CASES] = {
    0, 1, 0, 3, 0, 0, 5, 7, 0, 1, 1, 0, 3, 3, 0, 5, 7, 0, 1, 3, 0, 5, 1, 0
};
static uint64_t source[514] ALIGNED;
extern uint64_t destination[DEST_WORDS];

static size_t stride(size_t length) { return (length + 32 + 7) & ~(size_t)7; }

void initialise_benchmark(void)
{
    fill_pattern(source, ARRAY_WORDS(source));
    fill_guard(destination, ARRAY_WORDS(destination));
}

void benchmark(void)
{
    unsigned char *out = (unsigned char *)destination;
    const unsigned char *in = (const unsigned char *)source;
    size_t cursor = 0;
    for (size_t repeat = 0; repeat < REPEATS; ++repeat)
        for (size_t c = 0; c < CASES; ++c) {
            memcpy(out + cursor + 8 + offsets[c], in + offsets[c], lengths[c]);
            cursor += stride(lengths[c]);
        }
}
