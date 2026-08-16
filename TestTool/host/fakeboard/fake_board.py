"""A stand-in for the bootloader's UDP discovery and TCP flash channel.

Just enough of the protocol to drive the real IAPTool.exe end to end without a
board. Its whole purpose is the `getpubkey` handshake: the tool has to decide,
before sending a single byte of firmware, whether the key it would sign with is
the one this board will accept. That decision has five outcomes and none of
them are reachable on a real board without physically swapping keys.

This is NOT a bootloader model. It answers commands with fixed strings and does
no verification whatsoever -- what is under test is IAPTool's behaviour, not the
device's. Device behaviour is covered by the T/N/S cases against real hardware.

Usage:  fake_board.py <pubkey-hex | "unknown"> [seconds] [--port N]

  pubkey-hex   64-byte P-256 public key as 128 hex chars, returned by getpubkey
  "unknown"    answer getpubkey with "Unknown command", i.e. an old bootloader
  seconds      how long to stay up (default 25)
  --port       port to serve, default 56865 -- must match "server_port" in the
               local_config.json IAPTool reads, or the tool dials nothing
"""
import socket
import sys
import threading
import time

UID = "003300343132511039333639"
NONCE = "00112233445566778899aabbccddeeff"

_argv = sys.argv[1:]
PORT = 56865
if "--port" in _argv:
    i = _argv.index("--port")
    PORT = int(_argv[i + 1])
    del _argv[i:i + 2]

PUBKEY = _argv[0] if len(_argv) > 0 else "unknown"
LIFETIME = float(_argv[1]) if len(_argv) > 1 else 25.0


def log(m):
    print("[board] " + m, flush=True)


def udp_server(stop):
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.bind(("0.0.0.0", PORT))
    s.settimeout(0.5)
    while not stop.is_set():
        try:
            data, addr = s.recvfrom(1024)
        except socket.timeout:
            continue
        msg = data.decode(errors="replace").strip()
        log("UDP %r from %s" % (msg, addr))
        # The four keywords a real board answers (case N1). Missing any of them
        # here just makes the board look absent, which is a confusing way for a
        # key-match case to fail.
        if msg in ("openplc_server_where_r_y", "DISCOVER", "openplc_discover", "ping"):
            # name_uid_role_version -- the PC tool splits this on "_"
            s.sendto(("STM32H743_%s_BOOTLD_0.1.3" % UID).encode(), addr)
    s.close()


def handle_tcp(conn):
    state = "IDLE"
    expected = 0
    received = 0
    buf = b""
    while True:
        try:
            data = conn.recv(65536)
        except OSError:
            break
        if not data:
            break

        if state == "FLASH":
            received += len(data)
            conn.sendall(b"OK")
            log("data chunk %d bytes (%d/%d)" % (len(data), received, expected))
            if received >= expected:
                log("IMAGE FULLY RECEIVED")
            continue

        buf += data
        while b"\n" in buf:
            line, buf = buf.split(b"\n", 1)
            cmd = line.decode(errors="replace").strip()
            if not cmd:
                continue
            log("CMD %r" % cmd)

            if cmd == "ping":
                conn.sendall(b"OK")
            elif cmd == "getuid":
                conn.sendall(UID.encode())
            elif cmd == "getpubkey":
                if PUBKEY == "unknown":
                    conn.sendall(b"Unknown command")
                else:
                    conn.sendall(PUBKEY.encode())
            elif cmd == "getversion":
                conn.sendall(b"3")
            elif cmd == "authchallenge":
                conn.sendall(NONCE.encode())
            elif cmd.startswith("flash"):
                expected = int(cmd.split()[1])
                state = "FLASH"
                log("accepting %d bytes" % expected)
                conn.sendall(b"OK")
            else:
                conn.sendall(b"Unknown command")
    conn.close()


def tcp_server(stop):
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s.bind(("0.0.0.0", PORT))
    s.listen(4)
    s.settimeout(0.5)
    while not stop.is_set():
        try:
            conn, addr = s.accept()
        except socket.timeout:
            continue
        log("TCP connect from %s" % (addr,))
        handle_tcp(conn)
    s.close()


def main():
    stop = threading.Event()
    for target in (udp_server, tcp_server):
        t = threading.Thread(target=target, args=(stop,))
        t.daemon = True
        t.start()

    shown = PUBKEY[:16] + "..." if PUBKEY != "unknown" else "unknown"
    log("ready on %d, pubkey=%s" % (PORT, shown))
    try:
        time.sleep(LIFETIME)
    except KeyboardInterrupt:
        pass
    stop.set()


if __name__ == "__main__":
    main()
