SUITE := $(abspath $(dir $(lastword $(MAKEFILE_LIST))))
BUILD ?= $(SUITE)/build
NAMES := copy-small copy-stream copy-gather copy-scatter copy-chain copy-mixed
COMMON := $(SUITE)/support/main.c $(SUITE)/support/memory_test.c
HEADERS := $(SUITE)/support/memory_test.h
HOST_CC ?= cc
HOST_CFLAGS ?= -O2 -g -std=c11 -Wall -Wextra -Werror
CROSS_CC ?= riscv64-unknown-elf-gcc
ARCH ?= -march=rv64gc -mabi=lp64d -mcmodel=medany
# Point this at software/rocc-dma in the prepared Chipyard tree.
DMA_DIR ?= $(abspath $(SUITE)/../../software/rocc-dma)
TARGET_CFLAGS ?= -O2 -g -std=gnu11 $(ARCH)
TARGET_LDFLAGS ?= $(ARCH) -static -specs=htif_nano.specs
COPY_FLAGS := -fno-builtin-memcpy -fno-lto -U_FORTIFY_SOURCE -I$(SUITE)/support
WRAPS := -Wl,--wrap=atexit,--wrap=main,--wrap=verify_benchmark,--wrap=memcpy,--undefined=__wrap_memcpy

.PHONY: all baremetal host check
all: baremetal
# Each ELF lives at src/NAME/NAME (Embench layout).
baremetal: $(foreach name,$(NAMES),$(BUILD)/src/$(name)/$(name))
host: $(NAMES:%=$(BUILD)/native/%)
check: host
	python3 $(SUITE)/check.py --build-dir $(BUILD)/native

define BENCHMARK_RULES
$(BUILD)/native/$(1): $(SUITE)/src/$(1)/$(1).c $(COMMON) $(HEADERS) $(SUITE)/support/native_copy.c $(SUITE)/Makefile
	mkdir -p $$(@D)
	$(HOST_CC) $(HOST_CFLAGS) $(COPY_FLAGS) -DMEMORY_NATIVE -DMEMORY_TEST_NAME='"$(1)"' \
	  $(SUITE)/src/$(1)/$(1).c $(COMMON) $(SUITE)/support/native_copy.c \
	  -Wl,--wrap=memcpy -o $$@

$(BUILD)/src/$(1)/$(1): $(SUITE)/src/$(1)/$(1).c $(COMMON) $(HEADERS) $(SUITE)/Makefile \
    $(DMA_DIR)/rocc_dma.c $(DMA_DIR)/rocc_dma.h $(DMA_DIR)/memcpy_wrap.c \
    $(DMA_DIR)/embench/boardsupport.c $(DMA_DIR)/../../tests/atexit-workaround.c
	mkdir -p $$(@D)
	$(CROSS_CC) $(TARGET_CFLAGS) $(COPY_FLAGS) -fno-builtin-printf -fno-common \
	  -I$(DMA_DIR) -DMEMORY_TEST_NAME='"$(1)"' \
	  $(SUITE)/src/$(1)/$(1).c $(COMMON) $(DMA_DIR)/rocc_dma.c \
	  $(DMA_DIR)/memcpy_wrap.c $(DMA_DIR)/embench/boardsupport.c \
	  $(DMA_DIR)/../../tests/atexit-workaround.c $(TARGET_LDFLAGS) $(WRAPS) -o $$@
endef
$(foreach name,$(NAMES),$(eval $(call BENCHMARK_RULES,$(name))))
