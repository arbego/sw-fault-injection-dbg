idf_component_register(
    SRCS "fi_test_app.c" "modules/bit_flip.c" "modules/stuck_at.c"
    INCLUDE_DIRS "."
    REQUIRES freertos log
)
