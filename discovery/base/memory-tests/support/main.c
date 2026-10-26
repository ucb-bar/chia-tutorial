#include "memory_test.h"
#include <stdio.h>

/* Keep these calls in a separate TU for the existing --wrap hooks. */
int main(int argc, char **argv)
{
    (void)argc;
    (void)argv;
    initialise_board();
    initialise_benchmark();
    start_trigger();
    int result = benchmark();
    stop_trigger();
    int valid = verify_benchmark(result);
    printf("MEMORY_TEST name=%s checksum=%d verification_result=%d\n",
           MEMORY_TEST_NAME, result, valid);
#ifdef MEMORY_NATIVE
    void native_report(void);
    native_report();
#endif
    return valid ? 0 : 1;
}
