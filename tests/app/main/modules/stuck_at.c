#include "stuck_at.h"

volatile uint32_t fi_stuck_at_counter = 0;

void stuck_at_test_poll(void)
{
    fi_stuck_at_counter++;
}
