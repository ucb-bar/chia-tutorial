/* Fixed output storage and checksum oracle; candidates cannot edit this file. */
#include "memory_test.h"

uint64_t destination[4112] ALIGNED;

int benchmark_checksum(void)
{
    memory_test_observe(destination, ARRAY_WORDS(destination), 0);
    return (int)(checksum(destination, ARRAY_WORDS(destination)));
}

int verify_benchmark(int result)
{
    return memory_test_observing() || result == 1176047413;
}
