/* Fixed output storage and checksum oracle; candidates cannot edit this file. */
#include "memory_test.h"

uint64_t destination[20480] ALIGNED;

int benchmark_checksum(void)
{
    return (int)(checksum(destination, ARRAY_WORDS(destination)));
}

int verify_benchmark(int result)
{
    return result == 1142204022;
}
