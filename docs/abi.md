# ABI and maintenance

The raw API is `libretro.c`, re-exported as `c` by the package's default
library entry. `src/c.mach` follows the pinned [official header](../vendor/libretro/libretro.h).
The package defines no `retro_*` entry points and imports no external libretro
library. Each core implements and exports those 25 functions itself.

## Coverage

The pinned header is covered in full: 649 integer constants (including enum
members), 21 C enum types, 104 typedefs, 83 complete structures, four opaque
handle types, the framebuffer sentinel, and all four function-like API macros.
The 110 callback signatures include callbacks declared directly in structures.
This header has no union declarations, so no substitute union layouts are invented.

Interfaces include environment negotiation; system/AV and content information;
video, software/hardware framebuffers and context negotiation; audio including
float/multichannel callbacks; input, keyboards and controllers; memory maps and
serialization flags; core options v1/v2 and internationalization; disk control;
VFS including copying and authorized locations; logging/performance; rumble,
sensors, camera, location, LED, MIDI, microphone, power, netpacket, executable
memory, HDR/display metadata, video views, VR, decoding and upscaling.
Availability is negotiated with the frontend: a declaration does not promise
frontend support. Graphics backend companion headers such as `libretro_vulkan.h`
are separate APIs and are not included in this package.

## Representation and contracts

- C `bool` is `u8`. Write only 0 or 1; callbacks return canonical 0/1.
- C `int`/`unsigned` are `i32`/`u32`; fixed-width types retain their widths.
  C enums use `u32` on Linux/macOS and `i32` on Windows, matching Clang's
  platform C conventions. Enumerators remain C `int` constants.
- These targets use 64-bit `size_t`, `uintptr_t`, data pointers and function
  pointers. Other pointer widths are explicitly rejected.
- Records use native natural alignment and field order, without packing.
  C pointer qualifiers are not enforced by Mach. Read `const` pointers as
  read-only and honor the header's ownership, lifetime and thread rules.
- Empty records represent opaque C handles **only behind pointers**. Never
  allocate, instantiate, measure or perform pointer arithmetic on these types.
- Function types use the target's C calling convention. C-variadic logging
  uses `fun(retro_log_level, *u8, ...)`; promote tail integers to `i32`/`u32`
  and floats to `f64`. Mach 6.10.1 can call this type but cannot implement
  a C-variadic function body. A core consumes the frontend's logger.
- Header macros retain C integer types; explicit casts are needed when, for
  example, an `i32` environment constant is passed to a `u32` callback argument.
  Function-like macros are typed `u32` helpers. They accept unsigned IDs and
  perform the same bit operations; they are ordinary functions, not textual macros.

## Tests

With Mach 6.10.1, Python 3 and Clang installed:

```sh
mach build .
mach test . -a tests
python3 tools/test.py
python3 tools/test.py --profile release
```

The script checks generated declarations against the pinned header, builds the
library and checks that it exports no core entry points or test startup.
A temporary downstream consumer verifies both the bare `libretro` import and
`libretro.c`. The ABI suite compares 1,229 sizes, alignments, field offsets/widths
and enum signedness, all 649 constant values, and passes signed/unsigned,
pointer, size_t and floating-point values through all 110 callbacks. This
includes a C variadic logger reading integer, double and string arguments with
`va_arg`. Temporary ABI probes are removed after successful runs.

The separate `tests` artifact supplies startup for the two native Mach regression
tests covering essential layouts and macro semantics. It is excluded from the
normal library closure, including when importing the bare `libretro` module.

## Platforms

Linux x86_64 debug/release builds and the complete native ABI suite are verified
locally. The binding archives also cross-compile for Windows x86_64 and macOS
x86_64/aarch64 in both profiles. The native CI matrix passes the complete ABI suite on Linux x86_64, Windows
x86_64, Intel macOS and Apple Silicon macOS in both debug and release profiles.
The workflow is `.github/workflows/ci.yml`.

Mach 6.10.1's shared-library writer supports ELF only. The ABI tests use native
platform linkers to load temporary test probes on Windows/macOS. On macOS it
links the object members directly because Apple's linker rejects Mach's GNU
archive index. Mach's explicit
`#[symbol]` names are verbatim: C ABI symbols on Darwin need an underscore prefix.
This package supplies declarations; consumers own their core exports.

## Regeneration and sources

```sh
python3 tools/generate.py
mach fmt .
python3 tools/generate.py --check
```

Regeneration uses Clang's AST and a small native C program to obtain integer
constant values/types. It needs no Python packages or network. It rejects
unmapped types, bitfields and nested anonymous records instead of guessing.
Run the complete suite after changing the header, compiler or generator.
Keep the upstream license and update the pinned commit and checksum together.

Language decisions were checked against the installed compiler, the
[Mach 6.10.1 documentation](https://github.com/briar-systems/mach/tree/v6.10.1/doc/language),
[Mach std 9.5.0](https://github.com/briar-systems/mach-std/tree/v9.5.0), and
existing [mach-sdl](https://github.com/Wmakes/mach-sdl) and
[mach-raylib](https://github.com/Angluca/mach-raylib) manifest/binding conventions.
Current upstream compiler documentation can describe newer, incompatible versions.

SDL is not a dependency: libretro frontends own the video/audio/input backends
and supply callbacks to cores. A separate standalone frontend could use SDL.
The existing std dependency supplies startup for `mach test`; normal binding
builds do not import `std.runtime`.
