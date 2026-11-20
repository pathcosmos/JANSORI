"""Standard-library-only HF release gate for an isolated inference process.

The caller must authenticate final training/export provenance and pin the
expected manifest hash and source binding independently. This module verifies
the payload in this process; it does not turn a self-declared manifest into
proof of training completion. No weights are loaded and no GPU is initialized.
"""
import hashlib
import json
import os
from pathlib import Path
import re
import stat


def exact_path(path):
    path = Path(path)
    if '..' in path.parts:
        raise ValueError('Parent traversal in package path')
    path = path.absolute()
    if any(p.is_symlink() for p in [path, *path.parents]):
        raise ValueError('Package path symlink rejected')
    return path


def stamp(info):
    return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)


def inventory(directory):
    directory = exact_path(directory)
    result = {}
    for path in sorted(directory.iterdir()):
        info = path.lstat()
        if not stat.S_ISREG(info.st_mode):
            raise ValueError('Require a flat regular-file package')
        result[path.name] = stamp(info)
    return result


def digest(path, guard):
    path = exact_path(path)
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(fd, 'rb') as f:
        before = os.fstat(f.fileno())
        if not stat.S_ISREG(before.st_mode):
            raise ValueError('Cannot hash nonregular payload')
        h = hashlib.sha256()
        while block := f.read(8*1024*1024):
            h.update(block)
            guard()
        if stamp(before) != stamp(os.fstat(f.fileno())) or stamp(before) != stamp(path.lstat()):
            raise ValueError('Payload changed while hashing')
    return h.hexdigest()


def read_json(path):
    if path.stat().st_size > 16*1024*1024:
        raise ValueError('Unbounded package metadata')
    def unique(pairs):
        d = {}
        for key, value in pairs:
            if key in d:
                raise ValueError('Duplicate JSON key')
            d[key] = value
        return d
    return json.loads(path.read_text(), object_pairs_hook=unique)


class _LivePackage:
    def __init__(self, directory, stats, release, manifest_hash, guard):
        self.directory = directory
        self.release = release
        self.manifest_sha256 = manifest_hash
        self._stats = stats
        self._guard = guard

    def check(self):
        self._guard()
        if inventory(self.directory) != self._stats:
            raise ValueError('Verified package changed during runtime')


def authenticate_release(directory, *, expected_manifest_sha256,
                         expected_source_binding, expected_tokenizer_sha256, guard):
    """Fresh SHA of every file, then inode/stat reuse only in this live process."""
    if not callable(guard) or not expected_source_binding:
        raise ValueError('Require authenticated source binding and live guard')
    for value in (expected_manifest_sha256, expected_tokenizer_sha256):
        if not isinstance(value, str) or not re.fullmatch('[0-9a-f]{64}', value):
            raise ValueError('Require explicit expected SHA256 values')
    guard()
    directory = exact_path(directory)
    before = inventory(directory)
    manifest = directory/'release_manifest.json'
    if digest(manifest, guard) != expected_manifest_sha256:
        raise ValueError('Release manifest SHA differs from authenticated export')
    release = read_json(manifest)
    if (release['status'] != 'verified_hf_fp32_runtime_pending'
            or release['source_binding'] != expected_source_binding
            or any(release[k] is not False for k in
                   ('goal_complete', 'vllm_verified', 'ollama_verified', 'resumable_training_checkpoint'))):
        raise ValueError('Release source identity or scope differs')
    files = release['files']
    if not isinstance(files, dict) or not files:
        raise ValueError('Missing payload inventory')
    for name, record in files.items():
        if (not isinstance(name, str) or name in ('.', '..', 'release_manifest.json')
                or Path(name).name != name or '/' in name or '\\' in name
                or set(record) != {'bytes', 'sha256'} or type(record['bytes']) is not int
                or record['bytes'] < 0 or not isinstance(record['sha256'], str)
                or not re.fullmatch('[0-9a-f]{64}', record['sha256'])):
            raise ValueError('Invalid payload inventory entry')
    required = {'config.json', 'tokenizer.json', 'tokenizer_config.json',
                'conversion_report.json', 'numerical_validation.json'}
    if (not required <= files.keys() or not any(n.endswith('.safetensors') for n in files)
            or set(before) != set(files) | {'release_manifest.json'}):
        raise ValueError('Missing or unexpected package files')
    for name, record in files.items():
        path = directory/name
        if before[name][2] != record['bytes'] or digest(path, guard) != record['sha256']:
            raise ValueError('Published payload SHA/size differs: '+name)
    if (files['tokenizer.json']['sha256'] != expected_tokenizer_sha256
            or files['numerical_validation.json']['sha256'] != release['numerical_validation_sha256']):
        raise ValueError('Original tokenizer or numerical receipt SHA differs')
    numeric = read_json(directory/'numerical_validation.json')
    if (numeric['status'] != 'passed_cpu_fp32_conversion'
            or numeric['source_tokenizer_sha256'] != expected_tokenizer_sha256
            or numeric['package_files'] != {k:v for k,v in files.items() if k != 'numerical_validation.json'}):
        raise ValueError('Numerical validation no longer binds exact payload')
    live = _LivePackage(directory, before, release, expected_manifest_sha256, guard)
    live.check()
    return live
