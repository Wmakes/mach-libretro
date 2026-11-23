#!/usr/bin/env python3
"""Build the bindings and verify their ABI against libretro.h.
Uses native Mach plus a native C compiler (clang by default). No emulator dependencies.
"""
from pathlib import Path
import argparse
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import tempfile
import generate

ROOT = generate.ROOT
CC = os.environ.get('CC', 'clang')
OS = {'Linux': 'linux', 'Darwin': 'darwin', 'Windows': 'windows'}[platform.system()]
ARCH = {'AMD64': 'x86_64', 'x86_64': 'x86_64', 'arm64': 'aarch64', 'aarch64': 'aarch64'}[platform.machine()]
TARGET = f'{OS}-{ARCH}'
SUFFIX = {'linux': '.so', 'darwin': '.dylib', 'windows': '.dll'}[OS]


def command(*args, capture=False, **kwargs):
    args = [str(a) for a in args]
    print('+', ' '.join(args), flush=True)
    if capture:
        return subprocess.check_output(args, text=True, **kwargs)
    subprocess.run(args, check=True, **kwargs)


def link_archive(archive, output):
    # Platform linker only. The archive contains exclusively Mach objects.
    if OS == 'windows':
        command('lld-link', '/dll', '/noentry', '/nodefaultlib', f'/wholearchive:{archive}', f'/out:{output}')
    elif OS == 'darwin':
        # Mach writes a GNU archive index, which Apple's linker rejects.
        # Link the Mach-O members directly; the probe contains no C wrapper.
        data = archive.read_bytes()
        assert data.startswith(b'!<arch>\n'), 'Expected a Mach static archive'
        objects = []
        directory = output.parent/'probe-objects'
        directory.mkdir(exist_ok=True)
        offset = 8
        while offset < len(data):
            header = data[offset:offset+60]
            assert len(header) == 60 and header[58:] == b'`\n'
            length = int(header[48:58])
            member = data[offset+60:offset+60+length]
            if member.startswith(b'\xcf\xfa\xed\xfe'):
                obj = directory/f'{len(objects)}.o'
                obj.write_bytes(member)
                objects.append(obj)
            offset += 60 + length + (length & 1)
        assert objects, 'No Mach-O members found'
        command(CC, '-dynamiclib', *objects, '-o', output)
    else:
        command(CC, '-shared', '-nostdlib', '-Wl,-z,noexecstack', '-Wl,--whole-archive', archive, '-Wl,--no-whole-archive', '-o', output)


def library_symbols(profile):
    archive = ROOT/f'out/{TARGET}/{profile}/lib'/('mach-libretro.lib' if OS == 'windows' else 'mach-libretro.a')
    output = command('llvm-nm' if OS == 'windows' else 'nm', '-g', archive, capture=True)
    names = {line.split()[-1] for line in output.splitlines() if line.split()}
    entry_names = {n['name'] for n in generate.declarations() if n['kind'] == 'FunctionDecl'}
    forbidden = entry_names | {'_'+name for name in entry_names} | {'main', '_main', '_start', 'mainCRTStartup'}
    assert not names & forbidden, f'Core/startup symbols leaked into bindings: {names & forbidden}'
    assert 'std.runtime' not in output, 'Test startup leaked into normal library closure'
    print('Library symbols: no core entry points or test startup')


def compile_c(source, output):
    args = [CC, '-std=gnu11', '-Wall', '-Wextra', '-Werror', '-I', ROOT/'vendor/libretro', source, '-o', output]
    if OS == 'linux':
        args.append('-ldl')
    command(*args)


def qualified(ty):
    return re.sub(r'\b(retro_\w+|size_t|uintptr_t)\b', r'c.\1', generate.mach_type(ty))


def abi_sources(nodes):
    """C expectations come from sizeof/alignof/offsetof, never Mach output."""
    mach = ['use api: libretro.lib;', 'use c: libretro.c;', '#[export]', '#[symbol("bare_import")]', 'pub fun bare_import() u32 { ret api.c.RETRO_API_VERSION::u32; }']
    c = ['#include "libretro.h"', '#include <assert.h>', '#include <stdarg.h>', '#include <stdio.h>', '#include <string.h>',
         '#ifdef _WIN32', '#include <windows.h>', '#define OPEN(p) LoadLibraryA(p)', '#define SYM(h,n) GetProcAddress(h,n)', '#define CLOSE(h) FreeLibrary(h)',
         '#else', '#include <dlfcn.h>', '#define OPEN(p) dlopen(p, RTLD_NOW | RTLD_LOCAL)', '#define SYM(h,n) dlsym(h,n)', '#define CLOSE(h) dlclose(h)', '#endif',
         'static max_align_t storage[2048];', 'static unsigned calls;']
    checks = []
    layouts = []
    for n in nodes:
        if n['kind'] == 'RecordDecl' and n.get('completeDefinition'):
            fields = [f for f in n.get('inner', []) if f['kind'] == 'FieldDecl']
            layouts.append((n['name'], n['tagUsed']+' '+n['name'], fields))
        elif n['kind'] in ('EnumDecl', 'TypedefDecl'):
            if n['name'] == 'retro_microphone_t':  # incomplete opaque C type
                continue
            layouts.append((n['name'], ('enum ' if n['kind'] == 'EnumDecl' else '')+n['name'], []))
    count = 0
    for i, (name, cty, fields) in enumerate(layouts):
        cases = [(f'$size_of(c.{name})', f'sizeof({cty})'), (f'$align_of(c.{name})', f'_Alignof({cty})')]
        for f in fields:
            field = f['name']
            cases += [(f'$offset_of(c.{name}, {field})', f'offsetof({cty}, {field})'),
                      (f'$size_of({qualified(f["type"]["qualType"])})', f'sizeof((({cty}*)0)->{field})')]
        if cty.startswith('enum '):
            cases.append((f'((0i32 - 1)::c.{name} < 0)::u64', f'(({cty})-1 < ({cty})0)'))
        mach += ['#[export]', f'#[symbol("layout_{i}")]', f'pub fun layout_{i}(index: u32) u64 {{']
        for j, (mt, ct) in enumerate(cases):
            mach += [f'    if (index == {j}) {{ ret {mt}; }}']
            checks += [f'if (layout_{i}({j}) != (uint64_t)({ct})) {{ fprintf(stderr,"ABI mismatch: {name} {ct}\\n"); return 1; }}']
            count += 1
        mach += ['    ret 0xFFFFFFFFFFFFFFFFu64;', '}']
    typedefs = {n['name']: n['type']['qualType'] for n in nodes if n['kind'] == 'TypedefDecl'}
    callbacks = [(n['name'], n['type']['qualType']) for n in nodes if n['kind'] == 'TypedefDecl' and '(*)' in n['type']['qualType']]
    # Include callbacks declared directly as record fields, too.
    for n in nodes:
        if n['kind'] == 'RecordDecl':
            callbacks += [(n['name']+'.'+f['name'], f['type']['qualType']) for f in n.get('inner', []) if f['kind'] == 'FieldDecl' and '(*)' in f['type']['qualType']]
    cb_indices = {name: i for i, (name, _) in enumerate(callbacks)}
    for i, (_, ty) in enumerate(callbacks):
        c += ['typedef ' + ty.replace('(*)', f'(*cbtype_{i})') + ';']
    for i, (_, ty) in enumerate(callbacks):
        result, args = generate.split_function(ty)
        if '...' not in args:
            c += [f'static {result} callback_{i}(' + (', '.join(f'{t} a{j}' for j,t in enumerate(args)) or 'void') + ');']

    def value(ty, index):
        t = generate.mach_type(ty)
        if '(*)' in typedefs.get(ty, ''):
            return f'callback_{cb_indices[ty]}'
        if t.startswith('*') or t == 'ptr':
            return f'({ty})(void*)&storage[{index}]'
        if t in ('f32', 'f64'):
            return f'({ty}){index+1}.25'
        if t == 'u8' and ty == '_Bool':
            return 'true'
        if t.startswith('retro_'):
            return f'({ty})1'
        vals = {'u8':'0xd3', 'i8':'-73', 'u16':'0xc123', 'i16':'-12345', 'u32':'0x87654321U', 'i32':'-123456789',
                'u64':'0xfedcba9876543210ULL', 'i64':'-0x123456789LL', 'size_t':'0x123456789ULL', 'uintptr_t':'0x123456789ULL'}
        return f'({ty})({vals[t]})'

    for i, (name, ty) in enumerate(callbacks):
        result, args = generate.split_function(ty)
        mt = qualified(ty)
        if '...' in args:
            # Only retro_log_printf_t is variadic in the public header.
            assert name == 'retro_log_printf_t'
            mach += ['#[export]', f'#[symbol("invoke_{i}")]', f'pub fun invoke_{i}(cb: {mt}) {{ cb(c.RETRO_LOG_INFO::c.retro_log_level, "%d %f %s"::*u8, (0i32 - 17), 1.25f64, "ok"::*u8); }}']
            c += [f'static void log_callback(enum retro_log_level level, const char *fmt, ...) {{',
                  'assert(level == RETRO_LOG_INFO && strcmp(fmt,"%d %f %s") == 0);',
                  'va_list ap; va_start(ap,fmt); assert(va_arg(ap,int) == -17); assert(va_arg(ap,double) == 1.25); assert(strcmp(va_arg(ap,const char*),"ok") == 0); va_end(ap); calls++; }']
            checks += [f'void (*invoke_{i})(cbtype_{i}) = (void (*)(cbtype_{i}))SYM(handle,"invoke_{i}"); assert(invoke_{i}); invoke_{i}(log_callback);']
            continue
        params = ', '.join(f'a{j}: {qualified(t)}' for j,t in enumerate(args))
        suffix = (' ' + qualified(result)) if result != 'void' else ''
        mach += ['#[export]', f'#[symbol("invoke_{i}")]', f'pub fun invoke_{i}(cb: {mt}' + (', '+params if params else '') + ')' + suffix + ' {',
                 '    ' + ('ret ' if result != 'void' else '') + 'cb(' + ', '.join(f'a{j}' for j in range(len(args))) + ');', '}']
        c += [f'static {result} callback_{i}(' + (', '.join(f'{t} a{j}' for j,t in enumerate(args)) or 'void') + ') {']
        for j,t in enumerate(args):
            c += [f'assert(a{j} == {value(t,j)});']
        c += ['calls++;']
        if result != 'void':
            c += [f'return {value(result,1000+i)};']
        c += ['}']
        csig = f'{result} (*invoke_{i})(cbtype_{i}' + (', '+', '.join(args) if args else '') + ')'
        cast = f'{result} (*)(cbtype_{i}' + (', '+', '.join(args) if args else '') + ')'
        checks += [csig + f' = ({cast})SYM(handle,"invoke_{i}"); assert(invoke_{i});']
        call = f'invoke_{i}(callback_{i}' + (', '+', '.join(value(t,j) for j,t in enumerate(args)) if args else '') + ')'
        checks += [('assert('+call+' == '+value(result,1000+i)+');') if result != 'void' else call+';']
    c += ['int main(int argc, char **argv) { assert(argc == 2); void *handle = (void*)OPEN(argv[1]);', '#ifndef _WIN32', 'if (!handle) fprintf(stderr, "%s\\n", dlerror());', '#endif', 'assert(handle);']
    for i in range(len(layouts)):
        c += [f'uint64_t (*layout_{i})(unsigned) = (uint64_t (*)(unsigned))SYM(handle,"layout_{i}"); assert(layout_{i});']
    rows = generate.constants_and_enums(nodes)
    constants = [row[0] for row in rows if not row[0].startswith('@')]
    mach += ['#[export]', '#[symbol("constant_value")]', 'pub fun constant_value(index: u32) i64 {']
    for i, name in enumerate(constants):
        mach += [f'    if (index == {i}) {{ ret c.{name}::i64; }}']
        checks += [f'assert(constant_value({i}) == (int64_t)({name}));']
    mach += ['    ret 0;', '}']
    c += ['int64_t (*constant_value)(unsigned) = (int64_t (*)(unsigned))SYM(handle,"constant_value"); assert(constant_value);']
    c += ['unsigned (*bare_import)(void) = (unsigned (*)(void))SYM(handle,"bare_import"); assert(bare_import && bare_import() == RETRO_API_VERSION);']
    c += checks
    c += [f'assert(calls == {len(callbacks)}); CLOSE(handle); puts("ABI: {count} size/alignment/offset/signedness checks and {len(callbacks)} callback signatures, {len(constants)} constant values passed"); return 0; }}']
    return '\n'.join(mach)+'\n', '\n'.join(c)+'\n'


def abi_test(profile, temporary):
    nodes = generate.declarations()
    m, c = abi_sources(nodes)
    if OS == 'darwin':
        # Mach-O object symbols have a leading underscore. `#[symbol]` names
        # are emitted verbatim, so match the object name; dlsym then receives
        # the undecorated spelling after Mach-O's linker strips that prefix.
        m = re.sub(r'#\[symbol\("([^\"]+)"\)\]', lambda match: '#[symbol("_'+match[1]+'")]', m)
    (temporary/'src').mkdir()
    (temporary/'src/probe.mach').write_text(m)
    manifest = (ROOT/'mach.toml').read_text()
    manifest = manifest.replace('id = "libretro"', 'id = "abi"').replace('entry = "lib.mach"', 'entry = "probe.mach"')
    manifest = manifest[:manifest.index('[artifact.')] + f'''[artifact.probe]
kind = "static"
default = true
export = true
entry = "probe.mach"
out = "{{project.work}}/probe{{artifact.suffix}}"
targets = ["*"]
link = []
need = []

''' + manifest[manifest.index('[profile.debug]'):]
    # Use a path dependency, exactly as an independent consumer would.
    manifest = manifest.replace('git = "https://github.com/briar-systems/mach-std"\nversion = "^10"', 'path = '+json.dumps((ROOT/'dep/std').as_posix()))
    manifest += '\n[dep.libretro]\npath = '+json.dumps(ROOT.as_posix())+'\n'
    (temporary/'mach.toml').write_text(manifest)
    command('mach', 'dep', 'pull', temporary)
    command('mach', 'build', temporary, '-p', profile)
    out = temporary/f'out/{TARGET}/{profile}'
    library = out/('probe'+SUFFIX)
    link_archive(out/('probe.lib' if OS == 'windows' else 'probe.a'), library)
    cfile = temporary/'abi.c'; cfile.write_text(c)
    exe = temporary/('abi.exe' if OS == 'windows' else 'abi')
    compile_c(cfile, exe)
    command(exe, library)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--profile', choices=['debug','release'], default='debug')
    args = parser.parse_args()
    command(sys.executable, ROOT/'tools/generate.py', '--check')
    command('mach', 'info')
    command('mach', 'check', ROOT, '-p', args.profile)
    command('mach', 'build', ROOT, '-p', args.profile)
    library_symbols(args.profile)
    command('mach', 'test', ROOT, '-a', 'tests', '-p', args.profile)
    temporary = Path(tempfile.mkdtemp(prefix='mach-libretro-test-'))
    print(f'Test artifacts: {temporary}', flush=True)
    try:
        abi_test(args.profile, temporary)
    except BaseException:
        print(f'Failed test artifacts retained at {temporary}', flush=True)
        raise
    else:
        shutil.rmtree(temporary)
    print(f'PASS: {TARGET} {args.profile}')


if __name__ == '__main__':
    main()
