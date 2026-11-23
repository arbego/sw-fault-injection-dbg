# Python fault campaigns

Campaigns are ordinary Python files defining `run(session)`. Functions are
procedures; Python provides loops, conditions, imports, and assertions. The
`fi_dbg` API owns symbols, memory access, fault handles, and session cleanup.
The hardware tests use this same API and the existing fault model implementations.

From the `fi-dbg` directory, using the environment described in
[the hardware test instructions](../tests/README.md):

```sh
.venv/bin/python -m fi_dbg.run_campaign fi_dbg/campaigns/counters.py \
  --project tests/app --elf tests/app/build/fi_test_app.elf \
  --seed 42 --log /tmp/fi-campaign.json
```

This builds with the host ESP-IDF framework and flashes the test application
before running [the example](campaigns/counters.py). Omitting `--project`
uses the already flashed application; `--elf` must match it. When building a
project, the supplied ELF must match that project's generated ELF. Build output
uses the project's default `build` directory. Set `IDF_COMPONENT_MANAGER=1` for
projects needing managed dependencies; it defaults to disabled.

Scripts can also use the API directly from the repository root:

```python
from fi_dbg import FaultController

def hold_and_release(session, value):
    counter = "fi_stuck_at_counter"
    session.expect_increasing(counter)
    with session.stuck_at(counter, value, name="counter-hold") as fault:
        session.expect_value(counter, value, samples=3)
        assert fault.stats["triggers"] > 0
    session.expect_increasing(counter, min_delta=2)

with FaultController(elf="tests/app/build/fi_test_app.elf", seed=42) as session:
    hold_and_release(session, session.random.randint(2, 0x7FFFFFFF))
```

| Operation | Example |
| --- | --- |
| Resolve a symbol or byte offset | `session.address("fi_bit_flip_words", offset=4)` |
| Inspect symbol metadata | `session.symbol("fi_test_ready").size` |
| Read/write one value | `session.read(target)`, `session.write(target, 123)` |
| Read/write a block | `session.read_memory(target, count=3)`, `session.write_memory(target, [1, 2, 3])` |
| Read a consistent snapshot | `session.read_many(first, second)` |
| Flip once or repeatedly | `session.bit_flip(target, bit=3, count=2, interval=0.1)` |
| Flip a mask or seeded random bit | `session.bit_flip(target, mask=0x81)`, `session.bit_flip(target, random_bit=True)` |
| Hold a value | `fault = session.stuck_at(target, 123, name="hold")` |
| Release one fault | `fault.remove()` or `session.remove("hold")` |
| Inspect active faults | `session.faults` |
| Observe | `session.wait(0.2)`, `session.wait_until(lambda: session.read(target) == 123)` |
| Assert behavior | `session.expect_value(target, 123)`, `session.expect_increasing(target)` |
| Print and record a message | `session.log("Procedure complete")` |

Targets accept ELF symbol names, integer addresses, or hex address strings.
Memory access and bit-flips support `width=8`, `16`, or `32`; the default is 32.
A bit-flip returns a completed handle with `.injections` and `.stats`. It does
not undo the flipped value on cleanup. A stuck-at handle remains active until
removed, until its `with` block ends, or until the session closes. Releasing it
allows subsequent firmware writes; it does not restore the original value.
Fault setup never resets the target, so a procedure runs continuously. Resetting
belongs in target preparation; building and flashing with `--project` resets the
board before the procedure starts.

Memory observations briefly pause the CPU. They preserve stuck-at callbacks and
service a pending trapped write before resuming. Writes and bit-flips overlapping
an active stuck-at target are rejected; remove that fault first. Stuck-at remains
work in progress: the handler supports the test app's `S32I.N` stores, and the
ESP32-S3 has two hardware watchpoint slots per core. The API currently uses core 0.
Host waits and polling are unsuitable for precise hardware timing.

By default, each session starts a fresh local OpenOCD server and holds an exclusive
endpoint lock. Closing the session removes its faults and stops the server. The existing
supervisor stops OpenOCD even if the Python process is killed. Remote servers are
externally managed. The server settings and discovery variables are documented
in [the test instructions](../tests/README.md).

## Ad-hoc injection

Inject into the firmware already flashed on the target:

```sh
.venv/bin/python -m fi_dbg.inject stuck-at led_enabled 1 --elf build/fi_dbg.elf
.venv/bin/python -m fi_dbg.inject bit-flip led_enabled --bit 0 --elf build/fi_dbg.elf
```

The default starts a fresh local OpenOCD session (restarting an existing local
server), resets the target, waits two seconds for boot, and applies the fault.
`inject.py` never builds or flashes firmware and has no `--project` option.
`--elf` supplies symbol addresses only; it must match the flashed firmware.
Raw hex addresses work without an ELF.

Use `--no-reset` to inject into the current execution:

```sh
.venv/bin/python -m fi_dbg.inject stuck-at led_enabled 1 --elf build/fi_dbg.elf --no-reset
```

This reuses a responding OpenOCD server. If none exists locally, the injector
prints a note and starts one without requesting a target reset. A reused server
stays running after exit; a server started by this command is supervised and
stopped when the command ends, including if the process is killed. Remote
servers must already be running and remain externally managed.

Stuck-at remains active until Ctrl+C or until `--duration SECONDS` expires.
On normal exit, Ctrl+C, SIGTERM, or SIGHUP the injector removes its own faults,
resumes the target, and disconnects. Cleanup leaves faults owned by other clients
in place. A forced kill cannot run Python fault cleanup; the supervisor still
stops an owned OpenOCD server. Bit-flips support `--mask`, `--random`, `--width`,
`--count`, `--interval`, and `--seed`. Common options such as `--no-reset` and
`--elf` may appear before or after the fault subcommand. Direct invocation with
`.venv/bin/python fi_dbg/inject.py` also works.

`FaultController(..., reuse_openocd=True, reset=False)` exposes the same reuse
behavior to Python callers. `reset=True` requests reset during controller startup;
fault-model setup and injection methods remain reset-free. Supplying no ELF is
supported for raw addresses. Existing test and campaign defaults are unchanged.

The original `fi_dbg/fi.py` remains available and requires an already running
OpenOCD server:

```sh
.venv/bin/python -m fi_dbg.fi stuck-at led_enabled 1 --elf build/fi_dbg.elf
```

Direct invocation with `.venv/bin/python fi_dbg/fi.py` also works.

## Implementation layout

ELF parsing
lives in `fi_dbg/helpers/elf/elf_parser.py`; OpenOCD server and supervisor helpers
live in `fi_dbg/helpers/openocd/`. Fault model implementations live in
`fi_dbg/faults/`.

ESP-IDF discovery, host builds, JTAG flashing, target locks, and the OpenOCD
client wrapper live in `fi_dbg/helpers/target/esp.py`.

Every session prints its random seed. Use `session.random` for repeatable random
choices and `--seed` (or `FI_SEED`) to replay a runner's choices. Hardware timing
and counter values are not deterministic. Optional `--log` records the seed,
outcome, reads, writes, faults, assertions, and cleanup as JSON, including on
ordinary exceptions and Ctrl+C. A forced process kill cannot write that report.
Without the runner, the same events remain available in `session.history`.
