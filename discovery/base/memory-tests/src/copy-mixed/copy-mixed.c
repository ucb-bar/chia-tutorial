/* Variable-size message staging, including zero length, tails and fallback. */
#include "memory_test.h"

enum { CASES = 16, REPEATS = 16, DEST_WORDS = 19440 };
static const size_t lengths[CASES] = {
    0, 7, 8, 31, 63, 64, 65, 127, 128, 255, 256, 511, 512, 1024, 2048, 4096
};
static const size_t offsets[CASES] = {0, 1, 0, 3, 0, 0, 5, 7, 0, 1, 0, 3, 0, 0, 0, 0};
static uint64_t source[514] ALIGNED;
static uint64_t destination[DEST_WORDS] ALIGNED;

static size_t stride(size_t length) { return (length + 32 + 7) & ~(size_t)7; }

void initialise_benchmark(void)
{
    fill_pattern(source, ARRAY_WORDS(source));
    fill_guard(destination, ARRAY_WORDS(destination));
}

int benchmark(void)
{
    unsigned char *out = (unsigned char *)destination;
    const unsigned char *in = (const unsigned char *)source;
    size_t cursor = 0;
    for (size_t repeat = 0; repeat < REPEATS; ++repeat)
        for (size_t c = 0; c < CASES; ++c) {
            memcpy(out + cursor + 8 + offsets[c], in + offsets[c], lengths[c]);
            cursor += stride(lengths[c]);
        }
    return (int)checksum(destination, ARRAY_WORDS(destination));
}

int verify_benchmark(int result)
{
    if (!check_pattern(source, ARRAY_WORDS(source)))
        return 0;
    size_t cursor = 0;
    for (size_t repeat = 0; repeat < REPEATS; ++repeat)
        for (size_t c = 0; c < CASES; ++c) {
            size_t begin = 8 + offsets[c], end = begin + lengths[c];
            for (size_t word = 0; word < stride(lengths[c]) / 8; ++word) {
                size_t pos = word * 8;
                uint64_t expected = GUARD;
                if (pos >= begin && pos + 8 <= end) {
                    expected = pattern(word - 1);
                } else if (lengths[c] && pos < end && pos + 8 > begin) {
                    unsigned first = pos < begin ? (unsigned)(begin - pos) : 0;
                    unsigned last = pos + 8 > end ? (unsigned)(end - pos) : 8;
                    uint64_t mask = UINT64_MAX << (first * 8);
                    if (last < 8)
                        mask &= (UINT64_C(1) << (last * 8)) - 1;
                    expected = (GUARD & ~mask) | (pattern(word - 1) & mask);
                }
                if (destination[cursor + word] != expected)
                    return 0;
            }
            cursor += stride(lengths[c]) / 8;
        }
    return result == 1062611848;
}
