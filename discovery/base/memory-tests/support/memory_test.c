#include "memory_test.h"
#include <stdio.h>
#include <unistd.h>

/* Data rather than BSS: the evaluator supplies fresh runtime inputs by patching
 * this ELF record after compilation. Seed zero preserves the public workload. */
volatile struct memory_test_config memory_test_configuration
    __attribute__((section(".data.memory_test"))) = {"CHIA-MEMORY-0001", 0, 0};

static const uint64_t *observed_data;
static size_t observed_words;
static uint32_t observed_witness;

void memory_test_observe(const uint64_t *data, size_t words, uint32_t witness)
{
    observed_data = data;
    observed_words = words;
    observed_witness = witness;
}

static int report_bytes(const void *data, size_t bytes)
{
    const unsigned char *cursor = data;
    while (bytes) {
        ssize_t written = write(1, cursor, bytes);
        if (written <= 0)
            return 0;
        cursor += written;
        bytes -= (size_t)written;
    }
    return 1;
}

void memory_test_report(void)
{
    if (!memory_test_observing())
        return;
    static const char marker[] = "CHIA-OUTPUT-0001";
    uint64_t header[2] = {observed_words, observed_witness};
    /* HTIF's write syscall transfers the buffer in bulk. The worker hashes the
     * original bytes after execution, avoiding crypto or per-byte console IO
     * in the cycle-accurate simulated CPU. Both supported hosts are little-endian. */
    fflush(stdout);
    if (!report_bytes(marker, sizeof(marker) - 1) ||
        !report_bytes(header, sizeof(header)) ||
        !report_bytes(observed_data, observed_words * sizeof(uint64_t)) ||
        !report_bytes("\n", 1))
        return;
}

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
