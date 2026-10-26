/* Host-only traffic auditing and fault injection; never linked into target ELFs. */
#include <stddef.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

static size_t calls, bytes;
void *__real_memcpy(void *destination, const void *source, size_t length);

void *__wrap_memcpy(void *destination, const void *source, size_t length)
{
    const char *fault = getenv("MEMORY_COPY_FAULT");
    ++calls;
    bytes += length;
    if (fault && strcmp(fault, "skip") == 0)
        return destination;
    size_t copied = length;
    if (fault && strcmp(fault, "short") == 0 && length)
        --copied;
    __real_memcpy(destination, source, copied);
    if (fault && length) {
        if (strcmp(fault, "overrun") == 0)
            ((unsigned char *)destination)[length] ^= 1;
        if (strcmp(fault, "source") == 0)
            ((unsigned char *)source)[0] ^= 1;
    }
    return destination;
}

void native_report(void)
{
    printf("MEMORY_TRAFFIC calls=%zu bytes=%zu\n", calls, bytes);
}
