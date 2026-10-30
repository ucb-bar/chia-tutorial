#ifndef MEMORY_TEST_H
#define MEMORY_TEST_H

#include <stddef.h>
#include <stdint.h>
#include <string.h>

#define ALIGNED __attribute__((aligned(64)))
#define GUARD UINT64_C(0xd15ea5edcafef00d)
#define SALT UINT64_C(0x123456789abcdef0)
#define ARRAY_WORDS(a) (sizeof(a) / sizeof(uint64_t))

struct memory_test_config {
    unsigned char marker[16];
    uint64_t seed;
    uint64_t observe;
};
extern volatile struct memory_test_config memory_test_configuration;

static inline int memory_test_observing(void)
{
    return memory_test_configuration.observe != 0;
}

/* Unsigned arithmetic specifies the same reference data on host and RV64. */
static inline uint64_t pattern(size_t index)
{
    uint64_t value = ((uint64_t)(index + 1) * UINT64_C(0x9e3779b97f4a7c15)) ^ SALT;
    uint64_t seed = memory_test_configuration.seed;
    if (seed) {
        uint64_t mixed = seed ^ ((uint64_t)(index + 1) * UINT64_C(0x9e3779b97f4a7c15));
        mixed = (mixed ^ (mixed >> 30)) * UINT64_C(0xbf58476d1ce4e5b9);
        mixed = (mixed ^ (mixed >> 27)) * UINT64_C(0x94d049bb133111eb);
        value ^= mixed ^ (mixed >> 31);
    }
    return value;
}

static inline uint32_t fold(uint32_t hash, uint64_t word)
{
    return ((hash << 5) | (hash >> 27)) ^ (uint32_t)word ^ (uint32_t)(word >> 32);
}

void fill_pattern(uint64_t *data, size_t words);
void fill_guard(uint64_t *data, size_t words);
uint32_t checksum(const uint64_t *data, size_t words);
void memory_test_observe(const uint64_t *data, size_t words, uint32_t witness);
void memory_test_report(void);

void initialise_board(void);
void start_trigger(void);
void stop_trigger(void);
void initialise_benchmark(void);
void benchmark(void) __attribute__((noinline));
int benchmark_checksum(void);
int verify_benchmark(int result);

#endif
