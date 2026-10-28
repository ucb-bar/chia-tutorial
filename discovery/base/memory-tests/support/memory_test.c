#include "memory_test.h"

void fill_pattern(uint64_t *data, size_t words)
{
    for (size_t i = 0; i < words; ++i)
        data[i] = pattern(i);
}

void fill_guard(uint64_t *data, size_t words)
{
    for (size_t i = 0; i < words; ++i)
        data[i] = GUARD;
}

uint32_t checksum(const uint64_t *data, size_t words)
{
    uint32_t hash = UINT32_C(0x6d2b79f5);
    for (size_t i = 0; i < words; ++i)
        hash = fold(hash, data[i]);
    return hash & UINT32_C(0x7fffffff);
}

#ifdef MEMORY_NATIVE
void initialise_board(void) {}
void start_trigger(void) {}
void stop_trigger(void) {}
#endif
