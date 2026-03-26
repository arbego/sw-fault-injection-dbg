#include <stdio.h>
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "led_strip.h"

#define LED_GPIO 48
#define LED_BRIGHTNESS 3  // ~1% of 255

static led_strip_handle_t led_strip;

// Control variable for LED state
// 0 = LED off, non-zero = LED on
// This variable can be targeted for fault injection
volatile uint32_t led_enabled = 1;

static void configure_led(void)
{
    led_strip_config_t strip_config = {
        .strip_gpio_num = LED_GPIO,
        .max_leds = 1,
    };
    led_strip_rmt_config_t rmt_config = {
        .resolution_hz = 10 * 1000 * 1000,
        .flags.with_dma = false,
    };
    led_strip_new_rmt_device(&strip_config, &rmt_config, &led_strip);
    led_strip_clear(led_strip);
}

// Main LED control task - pinned to core 0 for debugging
static void led_task(void *arg)
{
    configure_led();
    uint32_t counter = 0;
    
    while (1) {
        // Toggle led_enabled every iteration
        led_enabled = !led_enabled;
        
        printf("[%lu] led_enabled = %lu\n", counter++, led_enabled);
        
        // LED state controlled by variable
        if (led_enabled) {
            led_strip_set_pixel(led_strip, 0, LED_BRIGHTNESS, LED_BRIGHTNESS, LED_BRIGHTNESS);
        } else {
            led_strip_set_pixel(led_strip, 0, 0, 0, 0);
        }
        led_strip_refresh(led_strip);
        
        vTaskDelay(pdMS_TO_TICKS(100));  // 100 ms delay
    }
}

void app_main(void)
{
    // Pin task to core 0 (where watchpoints work) for fault injection testing
    xTaskCreatePinnedToCore(
        led_task,           // Task function
        "led_task",         // Name
        4096,               // Stack size
        NULL,               // Parameters
        5,                  // Priority
        NULL,               // Task handle
        0                   // Core ID (0 = PRO_CPU)
    );
}
