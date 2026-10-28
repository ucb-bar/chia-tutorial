#ifndef MEMORY_TEST_H
#define MEMORY_TEST_H

#include <stddef.h>
#include <stdint.h>
#include <string.h>

#define ALIGNED __attribute__((aligned(64)))
#define GUARD UINT64_C(0xd15ea5edcafef00d)
#define SALT UINT64_C(0x123456789abcdef0)
#define ARRAY_WORDS(a) (sizeof(a) / sizeof(uint64_t))

/* Unsigned arithmetic specifies the same reference data on host and RV64. */
static inline uint64_t pattern(size_t index)
{
    return ((uint64_t)(index + 1) * UINT64_C(0x9e3779b97f4a7c15)) ^ SALT;
}

static inline uint32_t fold(uint32_t hash, uint64_t word)
{
    return ((hash << 5) | (hash >> 27)) ^ (uint32_t)word ^ (uint32_t)(word >> 32);
}

void fill_pattern(uint64_t *data, size_t words);
void fill_guard(uint64_t *data, size_t words);
uint32_t checksum(const uint64_t *data, size_t words);

void initialise_board(void);
void start_trigger(void);
void stop_trigger(void);
void initialise_benchmark(void);
void benchmark(void) __attribute__((noinline));
int benchmark_checksum(void);
int verify_benchmark(int result);

#endif
