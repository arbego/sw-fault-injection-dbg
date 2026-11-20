#include "stuck_at.h"

volatile uint32_t fi_stuck_at_value = 0;
volatile uint32_t fi_stuck_at_request = 0;

void stuck_at_test_poll(void)
{
    if (fi_stuck_at_request) {
        fi_stuck_at_value = 1;    // The stuck-at fault should prevent this write.
        fi_stuck_at_request = 0;  // Tell the host the write attempt is finished.
    }
}
