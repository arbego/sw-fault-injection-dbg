#!/usr/bin/env python3
"""
ESP32 Debug Interface Tool

Connects to OpenOCD's TCL interface to interact with the ESP32's
debug capabilities. Uses the official 'openocd' Python library.

Usage:
    1. Start OpenOCD: openocd -f board/esp32s3-builtin.cfg
    2. Run this tool: python esp_debug.py --host <host> <command>

OpenOCD exposes:
    - Telnet interface on port 4444 (commands)
    - GDB server on port 3333 (debugging)
    - TCL interface on port 6666 (scripting) <- we use this
"""

import re
import time
import argparse
from dataclasses import dataclass
from typing import Optional

from openocd import Client, ResetType


class OpenOCDError(Exception):
    """OpenOCD communication error."""
    pass


@dataclass
class CpuState:
    """CPU state information."""
    halted: bool
    pc: Optional[int] = None
    reason: Optional[str] = None


class ESPDebugger:
    """High-level ESP32 debug interface using official openocd library."""
    
    def __init__(self, host: str = "localhost", port: int = 6666):
        self.client = Client(host, port, timeout=10)
        self.connected = False
    
    def connect(self) -> bool:
        """Connect to OpenOCD."""
        self.client.connect()
        self.connected = True
        return True
    
    def disconnect(self):
        """Disconnect from OpenOCD."""
        self.client.close()
        self.connected = False
    
    def __enter__(self):
        self.connect()
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        self.disconnect()
    
    def info(self) -> dict:
        """Get debug interface information."""
        return {
            'version': self.client.execute('version'),
            'targets': self.client.execute('targets'),
        }
    
    def get_state(self) -> CpuState:
        """Get current CPU state."""
        targets = self.client.execute('targets')
        halted = 'halted' in targets.lower()
        pc = None
        reason = None
        
        if halted:
            try:
                pc_str = self.client.execute('reg pc')
                match = re.search(r'0x([0-9a-fA-F]+)', pc_str)
                if match:
                    pc = int(match.group(1), 16)
            except:
                pass
        
        return CpuState(halted=halted, pc=pc, reason=reason)
    
    def halt(self) -> CpuState:
        """Halt the CPU and return state."""
        self.client.halt()
        return self.get_state()
    
    def resume(self):
        """Resume CPU execution."""
        self.client.resume()
    
    def reset_and_halt(self) -> CpuState:
        """Reset target and halt at entry point."""
        self.client.reset(ResetType.HALT)
        time.sleep(0.1)
        return self.get_state()
    
    def reset_and_run(self):
        """Reset target and let it run."""
        self.client.reset(ResetType.RUN)
    
    def dump_memory(self, address: int, size: int) -> bytes:
        """Dump memory region."""
        # Read as bytes
        values = self.client.read_memory(address, size, 8)
        return bytes(values)
    
    def dump_registers(self) -> dict:
        """Dump all registers (CPU must be halted)."""
        regs = {}
        output = self.client.execute('reg')
        # Parse lines like: "pc (/32): 0x420159c4"
        for line in output.split('\n'):
            match = re.match(r'\s*(\w+)\s+\(/\d+\):\s+0x([0-9a-fA-F]+)', line)
            if match:
                name, value = match.groups()
                regs[name] = int(value, 16)
        return regs
    
    def read_register(self, name: str) -> Optional[int]:
        """Read a single register."""
        output = self.client.execute(f'reg {name}')
        match = re.search(r'0x([0-9a-fA-F]+)', output)
        if match:
            return int(match.group(1), 16)
        return None
    
    def read_pc(self) -> Optional[int]:
        """Read program counter."""
        return self.read_register("pc")
    
    def read_sp(self) -> Optional[int]:
        """Read stack pointer."""
        return self.read_register("a1")  # a1 is SP on Xtensa
    
    def write_word(self, address: int, value: int):
        """Write a 32-bit word to memory."""
        self.client.write_memory(address, [value], 32)
    
    def raw_command(self, cmd: str) -> str:
        """Execute a raw OpenOCD command."""
        return self.client.execute(cmd)


def print_hex_dump(data: bytes, start_addr: int, width: int = 16):
    """Pretty print a hex dump."""
    for i in range(0, len(data), width):
        chunk = data[i:i+width]
        hex_part = ' '.join(f'{b:02x}' for b in chunk)
        ascii_part = ''.join(chr(b) if 32 <= b < 127 else '.' for b in chunk)
        print(f"0x{start_addr + i:08x}: {hex_part:<{width*3}}  {ascii_part}")


def main():
    parser = argparse.ArgumentParser(description='ESP32 Debug Interface Tool')
    parser.add_argument('--host', default='localhost', help='OpenOCD host')
    parser.add_argument('--port', type=int, default=6666, help='OpenOCD TCL port')
    
    subparsers = parser.add_subparsers(dest='command', help='Commands')
    
    # Info command
    subparsers.add_parser('info', help='Show debug interface info')
    
    # State command
    subparsers.add_parser('state', help='Show CPU state')
    
    # Halt command
    subparsers.add_parser('halt', help='Halt the CPU')
    
    # Resume command
    subparsers.add_parser('resume', help='Resume CPU execution')
    
    # Reset command
    reset_parser = subparsers.add_parser('reset', help='Reset the target')
    reset_parser.add_argument('--halt', action='store_true', help='Halt after reset')
    
    # Registers command
    subparsers.add_parser('regs', help='Dump registers (CPU must be halted)')
    
    # Memory read command
    mem_parser = subparsers.add_parser('mem', help='Read memory')
    mem_parser.add_argument('address', help='Start address (hex)')
    mem_parser.add_argument('--size', type=int, default=64, help='Bytes to read')
    
    # Memory write command
    write_parser = subparsers.add_parser('write', help='Write memory')
    write_parser.add_argument('address', help='Address (hex)')
    write_parser.add_argument('value', help='Value to write (hex)')
    
    # Raw command
    raw_parser = subparsers.add_parser('raw', help='Send raw OpenOCD command')
    raw_parser.add_argument('cmd', nargs='+', help='Command to send')
    
    # Interactive mode
    subparsers.add_parser('shell', help='Interactive OpenOCD shell')
    
    args = parser.parse_args()
    
    if not args.command:
        parser.print_help()
        return
    
    try:
        with ESPDebugger(args.host, args.port) as dbg:
            if args.command == 'info':
                info = dbg.info()
                print("=== OpenOCD Info ===")
                print(info['version'])
                print("\n=== Targets ===")
                print(info['targets'])
            
            elif args.command == 'state':
                state = dbg.get_state()
                print(f"CPU State: {'HALTED' if state.halted else 'RUNNING'}")
                if state.pc is not None:
                    print(f"PC: 0x{state.pc:08x}")
                if state.reason:
                    print(f"Reason: {state.reason}")
            
            elif args.command == 'halt':
                state = dbg.halt()
                print(f"CPU halted at PC=0x{state.pc:08x}" if state.pc else "CPU halted")
            
            elif args.command == 'resume':
                dbg.resume()
                print("CPU resumed")
            
            elif args.command == 'reset':
                if args.halt:
                    state = dbg.reset_and_halt()
                    print(f"Reset and halted at PC=0x{state.pc:08x}" if state.pc else "Reset and halted")
                else:
                    dbg.reset_and_run()
                    print("Reset and running")
            
            elif args.command == 'regs':
                regs = dbg.dump_registers()
                if not regs:
                    print("No registers read. Is CPU halted?")
                else:
                    print("=== Registers ===")
                    for name, value in sorted(regs.items()):
                        print(f"  {name:8s} = 0x{value:08x}")
            
            elif args.command == 'mem':
                addr = int(args.address, 16)
                data = dbg.dump_memory(addr, args.size)
                print(f"Memory at 0x{addr:08x} ({len(data)} bytes):")
                print_hex_dump(data, addr)
            
            elif args.command == 'write':
                addr = int(args.address, 16)
                value = int(args.value, 16)
                dbg.write_word(addr, value)
                print(f"Wrote 0x{value:08x} to 0x{addr:08x}")
            
            elif args.command == 'raw':
                cmd = ' '.join(args.cmd)
                response = dbg.raw_command(cmd)
                print(response)
            
            elif args.command == 'shell':
                print("Interactive OpenOCD shell (type 'exit' to quit)")
                print("=" * 50)
                while True:
                    try:
                        cmd = input("openocd> ").strip()
                        if cmd.lower() in ('exit', 'quit', 'q'):
                            break
                        if cmd:
                            response = dbg.raw_command(cmd)
                            if response:
                                print(response)
                    except EOFError:
                        break
                    except KeyboardInterrupt:
                        print()
                        break
    
    except Exception as e:
        print(f"Error: {e}")
        print("\nMake sure OpenOCD is running:")
        print("  openocd -f board/esp32s3-builtin.cfg")
        return 1
    except KeyboardInterrupt:
        print("\nInterrupted")
        return 1


if __name__ == '__main__':
    exit(main() or 0)
