#!/usr/bin/env python3
"""Install the checksum-verified Mach release into a CI temporary directory."""
import hashlib
import json
import os
from pathlib import Path
import platform
import subprocess
import tarfile
import tempfile
import urllib.request
import zipfile

with urllib.request.urlopen('https://api.github.com/repos/briar-systems/mach/releases/latest', timeout=60) as response:
    release = json.load(response)
tag = release['tag_name']
VERSION = tag.removeprefix('v')
if not VERSION:
    raise SystemExit(f'Unexpected Mach release tag: {tag!r}')
os_name = {'Linux': 'linux', 'Darwin': 'darwin', 'Windows': 'windows'}[platform.system()]
arch = {'AMD64': 'x86_64', 'x86_64': 'x86_64', 'arm64': 'aarch64', 'aarch64': 'aarch64'}[platform.machine()]
base = f'https://github.com/briar-systems/mach/releases/download/{tag}'
name = f'mach-{VERSION}-{arch}-{os_name}'+('.zip' if os_name == 'windows' else '.tar.gz')
destination = Path(os.environ.get('RUNNER_TEMP', tempfile.gettempdir()))/f'libretro-mach-{VERSION}'
destination.mkdir(parents=True, exist_ok=True)
archive = destination/name
with urllib.request.urlopen(base+'/SHA256SUMS', timeout=60) as response:
    checksums = response.read().decode()
expected = {parts[1].lstrip('*'): parts[0] for line in checksums.splitlines() if len(parts := line.split()) == 2}[name]
with urllib.request.urlopen(base+'/'+name, timeout=60) as response:
    data = response.read()
if hashlib.sha256(data).hexdigest() != expected:
    raise SystemExit('Mach release checksum mismatch')
archive.write_bytes(data)
if name.endswith('.zip'):
    with zipfile.ZipFile(archive) as stream:
        stream.extractall(destination)
else:
    with tarfile.open(archive) as stream:
        stream.extractall(destination, filter='data')
exe = destination/('mach.exe' if os_name == 'windows' else 'mach')
subprocess.run([str(exe), 'info'], check=True)
if os.environ.get('GITHUB_PATH'):
    with open(os.environ['GITHUB_PATH'], 'a', encoding='utf-8') as stream:
        stream.write(str(destination)+'\n')
print(f'Checksum-verified compiler: {exe}')
