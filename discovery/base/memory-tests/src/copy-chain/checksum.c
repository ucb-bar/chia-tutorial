/* Fixed output storage and checksum oracle; candidates cannot edit this file. */
#include "memory_test.h"

uint64_t buffers[260] ALIGNED;
uint32_t witness;

int benchmark_checksum(void)
{
    return (int)((checksum(buffers, ARRAY_WORDS(buffers)) ^ witness) & UINT32_C(0x7fffffff));
}

int verify_benchmark(int result)
{
    return result == 1613899491;
}
