"""同じネットワークの相手さがしと、テキスト通話の接続。

・相手さがし: UDP ブロードキャストで「ここにいるよ」を数秒ごとに知らせ合う
・通話: かける側が相手の TCP ポートにつなぎ、1行1つの JSON をやりとりし続ける
    invite → accept / decline / busy → text(入力中の文字) / bye
サーバーは使わない。インターネットにも出ない(同じ LAN の中だけ)。
"""
from __future__ import annotations

import json
import socket
import threading
import time
import uuid

DISCOVERY_PORT = 50722
ANNOUNCE_EVERY = 3
PEER_TIMEOUT = 12
MAX_TEXT = 2000
MAGIC = "hitokoe-call/1"


def local_ipv4s() -> list[str]:
    ips = set()
    try:
        for ip in socket.gethostbyname_ex(socket.gethostname())[2]:
            if not ip.startswith("127."):
                ips.add(ip)
    except OSError:
        pass
    primary = None
    try:  # 外向きに使うアドレス(UDP の connect は実際には何も送らない)
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("10.255.255.255", 1))
        primary = s.getsockname()[0]
        s.close()
    except OSError:
        pass
    # ふだん使うアドレスを先頭に(仮想マシン用などのアドレスは後ろへ)
    rest = sorted(ips - {primary})
    return ([primary] if primary and not primary.startswith("127.") else []) + rest


class Call:
    """通話1本ぶんの接続。受け取ったものは on_event("call", (self, msg)) で知らせる。"""

    def __init__(self, sock: socket.socket, peer: dict, on_event, outgoing: bool):
        self.sock = sock
        self.peer = peer          # {"id", "name", "ip"}
        self.on_event = on_event
        self.outgoing = outgoing
        self.closed = False
        self.wlock = threading.Lock()
        sock.settimeout(None)

    def start(self):
        """受信を始める("invite"/"dialed" を知らせたあとに呼ぶので、順番が入れ替わらない)。"""
        threading.Thread(target=self._reader, daemon=True).start()
        return self

    def send(self, t: str, **kw):
        if self.closed:
            return
        data = (json.dumps({"t": t, **kw}, ensure_ascii=False) + "\n").encode("utf-8")
        try:
            with self.wlock:
                self.sock.sendall(data)
        except OSError:
            self._lost()

    def _reader(self):
        buf = b""
        try:
            while True:
                chunk = self.sock.recv(8192)
                if not chunk:
                    break
                buf += chunk
                if len(buf) > 1024 * 1024:
                    break
                while b"\n" in buf:
                    line, buf = buf.split(b"\n", 1)
                    try:
                        msg = json.loads(line.decode("utf-8"))
                    except ValueError:
                        continue
                    if isinstance(msg, dict):
                        if isinstance(msg.get("v"), str):
                            msg["v"] = msg["v"][:MAX_TEXT]
                        self.on_event("call", (self, msg))
        except OSError:
            pass
        self._lost()

    def _lost(self):
        if not self.closed:
            self.closed = True
            try:
                self.sock.close()
            except OSError:
                pass
            self.on_event("call", (self, {"t": "lost"}))

    def hang_up(self):
        if not self.closed:
            self.send("bye")
            self.closed = True
            try:
                self.sock.shutdown(socket.SHUT_RDWR)
                self.sock.close()
            except OSError:
                pass


class Lan:
    def __init__(self, name: str, on_event):
        """on_event(kind, data) は別スレッドから呼ばれる。
        kind: "peers" / "invite"(Call) / "call"((Call, msg)) / "dial_failed"(相手の名前)
        """
        self.id = uuid.uuid4().hex
        self.name = name
        self.on_event = on_event
        self.peers: dict[str, dict] = {}
        self.extra_targets: set[str] = set()
        self.lock = threading.Lock()
        self.running = True

        self.tcp = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.tcp.bind(("", 0))
        self.tcp.listen(8)
        self.port = self.tcp.getsockname()[1]

        self.udp = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.udp.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.udp.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        self.udp.bind(("", DISCOVERY_PORT))

        for target in (self._accept_loop, self._udp_loop, self._announce_loop):
            threading.Thread(target=target, daemon=True).start()

    # ------------------------------------------------------------ 相手さがし

    def _hello(self, kind: str = "hello") -> bytes:
        return json.dumps({"m": MAGIC, "t": kind, "id": self.id, "name": self.name,
                           "port": self.port}, ensure_ascii=False).encode("utf-8")

    def announce(self, kind: str = "hello"):
        targets = {"255.255.255.255"} | set(self.extra_targets)
        for ip in local_ipv4s():
            targets.add(".".join(ip.split(".")[:3] + ["255"]))
        pkt = self._hello(kind)
        for addr in targets:
            try:
                self.udp.sendto(pkt, (addr, DISCOVERY_PORT))
            except OSError:
                pass

    def add_ip(self, ip: str):
        socket.inet_aton(ip)  # 形式チェック
        self.extra_targets.add(ip)
        self.announce()

    def _announce_loop(self):
        while self.running:
            self.announce()
            now = time.time()
            with self.lock:
                gone = [k for k, p in self.peers.items() if now - p["seen"] > PEER_TIMEOUT]
                for k in gone:
                    del self.peers[k]
            if gone:
                self.on_event("peers", self.peer_list())
            time.sleep(ANNOUNCE_EVERY)

    def _udp_loop(self):
        while self.running:
            try:
                data, (ip, _) = self.udp.recvfrom(4096)
                d = json.loads(data.decode("utf-8"))
            except (OSError, ValueError):
                continue
            if not isinstance(d, dict) or d.get("m") != MAGIC or d.get("id") == self.id:
                continue
            pid = str(d.get("id"))[:64]
            if d.get("t") == "bye":
                with self.lock:
                    changed = self.peers.pop(pid, None) is not None
                if changed:
                    self.on_event("peers", self.peer_list())
                continue
            try:
                port = int(d["port"])
            except (KeyError, ValueError, TypeError):
                continue
            name = str(d.get("name") or "名無し")[:32]
            with self.lock:
                old = self.peers.get(pid)
                self.peers[pid] = {"id": pid, "name": name, "ip": ip, "port": port, "seen": time.time()}
                changed = old is None or old["name"] != name or old["ip"] != ip
            if changed:
                self.on_event("peers", self.peer_list())
                if old is None:
                    try:
                        self.udp.sendto(self._hello(), (ip, DISCOVERY_PORT))
                    except OSError:
                        pass

    def peer_list(self) -> list[dict]:
        with self.lock:
            return sorted((dict(p) for p in self.peers.values()), key=lambda p: (p["name"], p["ip"]))

    # ------------------------------------------------------------ 通話

    def _accept_loop(self):
        while self.running:
            try:
                conn, (ip, _) = self.tcp.accept()
            except OSError:
                continue
            threading.Thread(target=self._incoming, args=(conn, ip), daemon=True).start()

    def _incoming(self, conn: socket.socket, ip: str):
        """最初の1行(invite)だけ読んで、着信として知らせる。"""
        conn.settimeout(5)
        buf = b""
        try:
            while b"\n" not in buf and len(buf) < 8192:
                chunk = conn.recv(1024)
                if not chunk:
                    raise OSError
                buf += chunk
            line, rest = buf.split(b"\n", 1)
            d = json.loads(line.decode("utf-8"))
            if d.get("m") != MAGIC or d.get("t") != "invite":
                raise ValueError
        except (OSError, ValueError):
            conn.close()
            return
        peer = {"id": str(d.get("id"))[:64], "name": str(d.get("name") or "名無し")[:32], "ip": ip}
        call = Call(conn, peer, self.on_event, outgoing=False)
        self.on_event("invite", call)
        call.start()

    def dial(self, peer: dict):
        """相手に電話をかける(別スレッド)。つながったら Call を "dialed" で知らせる。"""
        def worker():
            try:
                s = socket.create_connection((peer["ip"], peer["port"]), timeout=5)
                s.sendall((json.dumps({"m": MAGIC, "t": "invite", "id": self.id, "name": self.name},
                                      ensure_ascii=False) + "\n").encode("utf-8"))
            except OSError:
                self.on_event("dial_failed", peer["name"])
                return
            call = Call(s, {"id": peer["id"], "name": peer["name"], "ip": peer["ip"]}, self.on_event, outgoing=True)
            self.on_event("dialed", call)
            call.start()
        threading.Thread(target=worker, daemon=True).start()

    def rename(self, name: str):
        self.name = name
        self.announce()

    def stop(self):
        self.running = False
        try:
            self.announce("bye")
        except OSError:
            pass
        for s in (self.udp, self.tcp):
            try:
                s.close()
            except OSError:
                pass
