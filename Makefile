# 816os build
#
#   make          build/816os.bin, the 32 KB ROM image for $8000-$FFFF
#   make test     build the test ROM and run the simulator tests
#   make run      run the demo ROM in the simulator (Ctrl-C to quit)
#   make clean

CA65    ?= ca65
LD65    ?= ld65
PYTHON  ?= python3

BUILD   := build
ASFLAGS := --cpu 65816 -I include -g

KERNEL_SRCS := $(wildcard kernel/*.s)
KERNEL_OBJS := $(patsubst kernel/%.s,$(BUILD)/kernel/%.o,$(KERNEL_SRCS))

DEMO_APPS   := ticker leds echo
TEST_APPS   := $(patsubst tests/apps/%.s,%,$(filter-out tests/apps/test_table.s,$(wildcard tests/apps/*.s)))

INCLUDES    := $(wildcard include/*.inc)

.PHONY: all test run clean
.SECONDARY:

all: $(BUILD)/816os.bin

# Kernel objects
$(BUILD)/kernel/%.o: kernel/%.s $(INCLUDES) | $(BUILD)/kernel
	$(CA65) $(ASFLAGS) -o $@ $<

# Process images: each linked on its own at $0000
$(BUILD)/apps/%.o: apps/%.s $(INCLUDES) | $(BUILD)/apps
	$(CA65) $(ASFLAGS) -o $@ $<

$(BUILD)/apps/%.o: tests/apps/%.s $(INCLUDES) | $(BUILD)/apps
	$(CA65) $(ASFLAGS) -o $@ $<

$(BUILD)/apps/%.bin: $(BUILD)/apps/%.o cfg/app.cfg
	$(LD65) -C cfg/app.cfg -m $(BUILD)/apps/$*.map -o $@ $<

# Program tables pull in the images with .incbin
$(BUILD)/demo_table.o: apps/demo_table.s $(DEMO_APPS:%=$(BUILD)/apps/%.bin)
	$(CA65) $(ASFLAGS) --bin-include-dir $(BUILD)/apps -o $@ $<

$(BUILD)/test_table.o: tests/apps/test_table.s $(TEST_APPS:%=$(BUILD)/apps/%.bin)
	$(CA65) $(ASFLAGS) --bin-include-dir $(BUILD)/apps -o $@ $<

# ROMs
$(BUILD)/816os.bin: $(KERNEL_OBJS) $(BUILD)/demo_table.o cfg/rom.cfg
	$(LD65) -C cfg/rom.cfg -m $(BUILD)/816os.map -Ln $(BUILD)/816os.lbl -o $@ $(KERNEL_OBJS) $(BUILD)/demo_table.o

$(BUILD)/test.bin: $(KERNEL_OBJS) $(BUILD)/test_table.o cfg/rom.cfg
	$(LD65) -C cfg/rom.cfg -m $(BUILD)/test.map -Ln $(BUILD)/test.lbl -o $@ $(KERNEL_OBJS) $(BUILD)/test_table.o

$(BUILD)/kernel $(BUILD)/apps:
	mkdir -p $@

test: $(BUILD)/816os.bin $(BUILD)/test.bin
	$(PYTHON) -m unittest discover -s tests -v

run: $(BUILD)/816os.bin
	$(PYTHON) tools/sim816.py $(BUILD)/816os.bin

clean:
	rm -rf $(BUILD)
