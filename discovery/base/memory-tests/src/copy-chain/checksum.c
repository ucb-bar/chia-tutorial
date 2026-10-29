/* Fixed output storage and checksum oracle; candidates cannot edit this file. */
#include "memory_test.h"

uint64_t buffers[132] ALIGNED;
uint32_t witness;

int benchmark_checksum(void)
{
    memory_test_observe(buffers, ARRAY_WORDS(buffers), witness);
    return (int)((checksum(buffers, ARRAY_WORDS(buffers)) ^ witness) & UINT32_C(0x7fffffff));
}

int verify_benchmark(int result)
{
    return memory_test_observing() || result == 2062308453;
}
