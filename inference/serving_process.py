"""Exclusive loopback listener and live pid/start-time/fd ownership checks."""
import os
from pathlib import Path
import socket


def bind_loopback():
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        # The pinned upstream helper enables REUSEPORT. Use an exclusively
        # bound ephemeral port so another server cannot share this address.
        sock.bind(('127.0.0.1', 0))
        return sock
    except BaseException:
        sock.close()
        raise


def process_ticks(pid):
    fields = Path(f'/proc/{pid}/stat').read_text().rsplit(')', 1)[1].split()
    if fields[0] == 'Z':
        raise ValueError('Server process is a zombie')
    return int(fields[19])


def listener_record(sock, *, owner):
    host, port = sock.getsockname()
    if (sock.family != socket.AF_INET or host != '127.0.0.1'
            or sock.getsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT) != 0 or not owner):
        raise ValueError('Require an exclusively bound loopback socket and owner')
    return dict(pid=os.getpid(), start_ticks=process_ticks(os.getpid()), owner=owner,
                host=host, port=port, fd=sock.fileno(), inode=os.fstat(sock.fileno()).st_ino)


def verify_listener(record, handle, command, *, listening=True):
    if (record['pid'] != handle['pid'] or record['start_ticks'] != handle['process']['start_ticks']
            or record['owner'] != handle['owner'] or handle['command'] != command
            or record['host'] != '127.0.0.1' or type(record['port']) is not int
            or not 1 <= record['port'] <= 65535 or type(record['fd']) is not int or record['fd'] < 0
            or type(record['inode']) is not int or record['inode'] < 1):
        raise ValueError('Listener ownership receipt differs')
    proc = Path(f"/proc/{record['pid']}")
    args = (proc/'cmdline').read_bytes().decode().rstrip('\0').split('\0')
    expected = command[3:] if command[:2] == ['taskset', '-c'] else command
    env = (proc/'environ').read_bytes().split(b'\0')
    if (process_ticks(record['pid']) != record['start_ticks'] or args != expected
            or ('LLM_HORIZON_DIAGNOSTIC_OWNER='+handle['owner']).encode() not in env
            or os.readlink(proc/'fd'/str(record['fd'])) != f"socket:[{record['inode']}]"):
        raise ValueError('Actual listener process/fd ownership changed')
    if listening:
        address = f"0100007F:{record['port']:04X}"
        matches = []
        for line in (proc/'net/tcp').read_text().splitlines()[1:]:
            fields = line.split()
            if fields[1] == address and fields[3] == '0A':
                matches.append(int(fields[9]))
        if matches != [record['inode']]:
            raise ValueError('Owned loopback listener is not uniquely listening')
    # Boundaries may race exit; repeat identity after reading the socket table.
    if process_ticks(record['pid']) != record['start_ticks']:
        raise ValueError('Server identity changed during listener check')
    return dict(pid=record['pid'], start_ticks=record['start_ticks'], inode=record['inode'],
                host=record['host'], port=record['port'], listening=listening)


def listener_ready(record, handle, command):
    verify_listener(record, handle, command, listening=False)
    address = f"0100007F:{record['port']:04X}"
    rows = [r.split() for r in Path(f"/proc/{record['pid']}/net/tcp").read_text().splitlines()[1:]]
    matches = [int(r[9]) for r in rows if r[1] == address and r[3] == '0A']
    if not matches:
        return False
    verify_listener(record, handle, command)
    return True
