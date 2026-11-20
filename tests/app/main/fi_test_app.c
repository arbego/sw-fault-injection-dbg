#include <stdint.h>
#include <stdlib.h>

#include "esp_log.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "modules/bit_flip.h"

// The host checks this signature before writing to the test memory.
volatile uint32_t fi_test_ready = 0;
volatile uint32_t fi_test_heartbeat = 0;
volatile uint32_t fi_test_request = 0;
volatile uint32_t fi_test_ack = 0;

static void test_task(void *arg)
{
    (void)arg;
    fi_test_ready = 0x46495431; // "FIT1": test firmware protocol version 1
    ESP_LOGI("fi_test", "Ready for JTAG fault injection tests");

    while (1) {
        bit_flip_test_poll();
        fi_test_heartbeat++;
        fi_test_ack = fi_test_request;
        vTaskDelay(pdMS_TO_TICKS(10));
    }
}

void app_main(void)
{
    if (xTaskCreatePinnedToCore(
        test_task, "fi_test", 4096, NULL, 5, NULL, 0
    ) != pdPASS) {
        ESP_LOGE("fi_test", "Could not create test task");
        abort();
    }
}
