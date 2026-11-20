#pragma once

#include <stdint.h>

// Inject into words[1]. The other words detect writes outside the target.
extern volatile uint32_t fi_bit_flip_words[3];
extern volatile uint32_t fi_bit_flip_observed;

void bit_flip_test_poll(void);
