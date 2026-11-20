#pragma once

#include <stdint.h>

extern volatile uint32_t fi_stuck_at_value;
extern volatile uint32_t fi_stuck_at_request;

void stuck_at_test_poll(void);
