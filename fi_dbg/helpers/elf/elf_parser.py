#!/usr/bin/env python3
"""
Generic ELF Parser.
Extracts memory sections with addresses, sizes, and attributes from any ELF file.
"""

import argparse
import sys
from pathlib import Path
from dataclasses import dataclass
from typing import List, Optional

try:
    from elftools.elf.elffile import ELFFile
    from elftools.elf.sections import SymbolTableSection
except ImportError:
    print("Error: pyelftools not installed. Run: pip install pyelftools")
    sys.exit(1)


@dataclass
class MemorySection:
    name: str
    address: int
    size: int
    section_type: str
    flags: str
    
    @property
    def end_address(self) -> int:
        return self.address + self.size
    
    def __str__(self) -> str:
        return f"{self.name:30s} 0x{self.address:08x} - 0x{self.end_address:08x}  size={self.size:8d} ({self.size:6x}h)  [{self.flags}]"


@dataclass  
class Symbol:
    name: str
    address: int
    size: int
    symbol_type: str
    section: str


class ElfParser:
    def __init__(self, elf_path: str):
        self.elf_path = Path(elf_path)
        if not self.elf_path.exists():
            raise FileNotFoundError(f"ELF file not found: {elf_path}")
        
        with open(self.elf_path, 'rb') as f:
            self.elf = ELFFile(f)
            self._sections = list(self._parse_sections())
            self._symbols = list(self._parse_symbols())
    
    def _parse_sections(self) -> List[MemorySection]:
        """Parse all sections from the ELF file."""
        with open(self.elf_path, 'rb') as f:
            elf = ELFFile(f)
            for section in elf.iter_sections():
                flags = self._decode_flags(section['sh_flags'])
                yield MemorySection(
                    name=section.name,
                    address=section['sh_addr'],
                    size=section['sh_size'],
                    section_type=section['sh_type'],
                    flags=flags,
                )
    
    def _parse_symbols(self) -> List[Symbol]:
        """Parse symbols from the ELF file."""
        with open(self.elf_path, 'rb') as f:
            elf = ELFFile(f)
            for section in elf.iter_sections():
                if isinstance(section, SymbolTableSection):
                    for symbol in section.iter_symbols():
                        if symbol['st_value'] and symbol.name:
                            yield Symbol(
                                name=symbol.name,
                                address=symbol['st_value'],
                                size=symbol['st_size'],
                                symbol_type=symbol['st_info']['type'],
                                section=symbol['st_shndx'],
                            )
    
    def _decode_flags(self, flags: int) -> str:
        """Decode section flags to human-readable string."""
        result = []
        if flags & 0x1:  # SHF_WRITE
            result.append('W')
        if flags & 0x2:  # SHF_ALLOC
            result.append('A')
        if flags & 0x4:  # SHF_EXECINSTR
            result.append('X')
        return ''.join(result) if result else '-'
    
    def get_sections(self, 
                     min_size: int = 0,
                     with_address: bool = True,
                     executable_only: bool = False,
                     writable_only: bool = False) -> List[MemorySection]:
        """Get filtered list of sections."""
        sections = self._sections
        
        if with_address:
            sections = [s for s in sections if s.address != 0]
        if min_size > 0:
            sections = [s for s in sections if s.size >= min_size]
        if executable_only:
            sections = [s for s in sections if 'X' in s.flags]
        if writable_only:
            sections = [s for s in sections if 'W' in s.flags]
            
        return sorted(sections, key=lambda s: s.address)
    
    def get_symbols(self, 
                    name_filter: Optional[str] = None,
                    symbol_type: Optional[str] = None) -> List[Symbol]:
        """Get filtered list of symbols."""
        symbols = self._symbols
        
        if name_filter:
            symbols = [s for s in symbols if name_filter in s.name]
        if symbol_type:
            symbols = [s for s in symbols if s.symbol_type == symbol_type]
            
        return sorted(symbols, key=lambda s: s.address)
    
    def find_section_by_address(self, address: int) -> Optional[MemorySection]:
        """Find which section contains a given address."""
        for section in self.get_sections():
            if section.address <= address < section.end_address:
                return section
        return None
    
    def find_symbol_by_address(self, address: int) -> Optional[Symbol]:
        """Find symbol at or near the given address."""
        closest = None
        for symbol in self._symbols:
            if symbol.address <= address:
                if closest is None or symbol.address > closest.address:
                    closest = symbol
        return closest
    
    def find_symbol_by_name(self, name: str) -> Optional[Symbol]:
        """Find symbol by exact name."""
        for symbol in self._symbols:
            if symbol.name == name:
                return symbol
        return None
    
    def get_variable_address(self, name: str) -> Optional[int]:
        """Get the memory address of a variable by name.
        
        Returns:
            Address of the variable, or None if not found
        """
        symbol = self.find_symbol_by_name(name)
        if symbol:
            return symbol.address
        return None


def main():
    parser = argparse.ArgumentParser(description='Parse ELF files')
    parser.add_argument('elf_file', help='Path to ELF file')
    parser.add_argument('-s', '--sections', action='store_true', 
                        help='Show all memory sections')
    parser.add_argument('-x', '--executable', action='store_true',
                        help='Show only executable sections')
    parser.add_argument('-w', '--writable', action='store_true',
                        help='Show only writable sections')
    parser.add_argument('--min-size', type=int, default=0,
                        help='Minimum section size to display')
    parser.add_argument('-f', '--find', type=str,
                        help='Find section/symbol containing address (hex)')
    parser.add_argument('--symbols', type=str, nargs='?', const='',
                        help='Show symbols (optionally filter by name)')
    parser.add_argument('-l', '--lookup', type=str,
                        help='Look up symbol by exact name and print address')
    parser.add_argument('-j', '--json', action='store_true',
                        help='Output as JSON')
    
    args = parser.parse_args()
    
    try:
        elf = ElfParser(args.elf_file)
    except FileNotFoundError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)
    
    if args.json:
        import json
        sections = elf.get_sections(min_size=args.min_size, 
                                    executable_only=args.executable,
                                    writable_only=args.writable)
        data = [{
            'name': s.name,
            'address': hex(s.address),
            'end_address': hex(s.end_address),
            'size': s.size,
            'flags': s.flags,
            'type': s.section_type,
        } for s in sections]
        print(json.dumps(data, indent=2))
        return
    
    if args.find:
        address = int(args.find, 16)
        section = elf.find_section_by_address(address)
        symbol = elf.find_symbol_by_address(address)
        print(f"Address: 0x{address:08x}")
        if section:
            print(f"Section: {section}")
        else:
            print("Section: not found")
        if symbol:
            offset = address - symbol.address
            print(f"Nearest symbol: {symbol.name} + 0x{offset:x}")
        return
    
    if args.lookup:
        symbol = elf.find_symbol_by_name(args.lookup)
        if symbol:
            print(f"0x{symbol.address:08x}")
        else:
            # Try with different name formats
            for prefix in ['', 'g_', 's_']:
                alt_name = prefix + args.lookup
                symbol = elf.find_symbol_by_name(alt_name)
                if symbol:
                    print(f"0x{symbol.address:08x}  (found as '{symbol.name}')")
                    return
            print(f"Symbol not found: {args.lookup}", file=sys.stderr)
            sys.exit(1)
        return
    
    if args.symbols is not None:
        name_filter = args.symbols if args.symbols else None
        # Show both functions and objects (variables)
        funcs = elf.get_symbols(name_filter=name_filter, symbol_type='STT_FUNC')
        objs = elf.get_symbols(name_filter=name_filter, symbol_type='STT_OBJECT')
        
        if objs:
            print(f"\nVariables ({len(objs)} found):")
            print("-" * 80)
            for sym in objs[:50]:
                print(f"  0x{sym.address:08x}  {sym.name} ({sym.size} bytes)")
            if len(objs) > 50:
                print(f"  ... and {len(objs) - 50} more")
        
        if funcs:
            print(f"\nFunctions ({len(funcs)} found):")
            print("-" * 80)
            for sym in funcs[:100]:
                print(f"  0x{sym.address:08x}  {sym.name}")
            if len(funcs) > 100:
                print(f"  ... and {len(funcs) - 100} more")
        return
    
    # Default: show sections
    sections = elf.get_sections(
        min_size=args.min_size,
        executable_only=args.executable,
        writable_only=args.writable,
    )
    
    print(f"\nMemory Sections ({len(sections)} with addresses):")
    print("=" * 100)
    print(f"{'Name':30s} {'Address Range':27s} {'Size':>16s}  Flags")
    print("-" * 100)
    
    for section in sections:
        print(section)
    
    # Summary
    total_size = sum(s.size for s in sections)
    print("-" * 100)
    print(f"Total: {total_size:,} bytes")


if __name__ == '__main__':
    main()
