#include "bit_flip.h"

volatile uint32_t fi_bit_flip_words[3] = {
    0x13579BDF, 0xA55A96C3, 0x2468ACE0,
};
volatile uint32_t fi_bit_flip_observed = 0;

void bit_flip_test_poll(void)
{
    // Only the JTAG test driver writes words[]. Keep the injected value stable
    // and publish what the running firmware actually reads from the target.
    fi_bit_flip_observed = fi_bit_flip_words[1];
}
