"""ひとこえ通話 ― 同じネットワークでできる「テキスト通話」

相手に通話をかけて、つながったらお互いが打っている文字がリアルタイムで見える。
Enter で消して次の言葉へ。リアクションも送れる。履歴は残さない(画面にも、ファイルにも)。
"""
from __future__ import annotations

import argparse
import ctypes
import json
import os
import queue
import sys
import time
import tkinter as tk
from tkinter import font as tkfont
from tkinter import messagebox, simpledialog, ttk

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import lan  # noqa: E402

APP_DIR = os.path.join(os.environ.get("APPDATA", os.path.expanduser("~")), "HitokoeCall")
TITLE = "ひとこえ通話"
RING_TIMEOUT = 30  # 秒

GREEN = "#2E7D4F"
RED = "#D9443B"
REACTIONS = ["👍", "❤️", "🎉", "😂", "😮"]


def dpi_scale() -> float:
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        pass
    try:
        return ctypes.windll.user32.GetDpiForSystem() / 96
    except Exception:
        return 1.0


def single_instance() -> bool:
    try:
        ctypes.windll.kernel32.CreateMutexW(None, False, "Local\\HitokoeCall.SingleInstance")
        return ctypes.windll.kernel32.GetLastError() != 183
    except Exception:
        return True


def beep(kind: str = "ring"):
    try:
        import winsound
        winsound.MessageBeep(winsound.MB_ICONASTERISK if kind == "ring" else winsound.MB_OK)
    except Exception:
        pass


THEMES = {
    "黒": {"bg": "#000000", "card_them": "#2B2B2B", "card_me": "#6B6B6B", "text": "#FFFFFF",
           "muted": "#9A9A9A", "hint": "#666666", "ring": "#2ECC71", "ring_talk": "#3A6B4E",
           "ring_idle": "#555555", "hover": "#222222", "disabled": "#444444", "closed_btn": "#444444",
           "list_bg": "#161616", "list_sel": "#333333", "head_bg": "#222222", "dark": True},
    "白": {"bg": "#F3F3F3", "card_them": "#FFFFFF", "card_me": "#E1E1E1", "text": "#1A1A1A",
           "muted": "#7A7A7A", "hint": "#9A9A9A", "ring": "#2ECC71", "ring_talk": "#A8DDBE",
           "ring_idle": "#CCCCCC", "hover": "#E6E6E6", "disabled": "#C0C0C0", "closed_btn": "#999999",
           "list_bg": "#FFFFFF", "list_sel": "#DCE9F7", "head_bg": "#E9E9E9", "dark": False},
}
P = dict(THEMES["黒"])  # いまの見た目(設定で入れ替える)


def set_titlebar(win: tk.Misc, dark: bool):
    """Windows 10/11 のタイトルバーも白/黒に合わせる。"""
    try:
        win.update_idletasks()
        hwnd = ctypes.windll.user32.GetParent(win.winfo_id())
        val = ctypes.c_int(1 if dark else 0)
        for attr in (20, 19):  # DWMWA_USE_IMMERSIVE_DARK_MODE(新旧)
            if ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, attr, ctypes.byref(val), ctypes.sizeof(val)) == 0:
                break
    except Exception:
        pass
AVATAR_COLORS = ["#E07A5F", "#3D85C6", "#8E7CC3", "#E69138", "#6AA84F", "#C27BA0", "#45818E"]


def rounded_rect(cv: tk.Canvas, x1, y1, x2, y2, r, **kw):
    pts = [x1 + r, y1, x2 - r, y1, x2, y1, x2, y1 + r, x2, y2 - r, x2, y2,
           x2 - r, y2, x1 + r, y2, x1, y2, x1, y2 - r, x1, y1 + r, x1, y1]
    return cv.create_polygon(pts, smooth=True, splinesteps=24, **kw)


def avatar_color(name: str) -> str:
    return AVATAR_COLORS[sum(map(ord, name)) % len(AVATAR_COLORS)]


# ================================================================ 着信

class IncomingWindow(tk.Toplevel):
    def __init__(self, app: "App", call: lan.Call):
        super().__init__(app.root)
        self.app, self.call = app, call
        px = app.px
        self.title("着信")
        self.configure(bg=P["bg"])
        self.resizable(False, False)
        w, h = px(320), px(230)
        x = app.root.winfo_screenwidth() - w - px(30)
        y = app.root.winfo_screenheight() - h - px(90)
        self.geometry(f"{w}x{h}+{x}+{y}")
        self.attributes("-topmost", True)
        self.protocol("WM_DELETE_WINDOW", self.decline)

        name = call.peer["name"]
        r = px(26)
        av = tk.Canvas(self, width=2 * r + px(8), height=2 * r + px(8), bg=P["bg"], highlightthickness=0)
        av.pack(pady=(px(16), px(4)))
        c = r + px(4)
        av.create_oval(c - r - 3, c - r - 3, c + r + 3, c + r + 3, outline=P["ring"], width=3)
        av.create_oval(c - r + 2, c - r + 2, c + r - 2, c + r - 2, fill=avatar_color(name), outline="")
        av.create_text(c, c, text=name[:1], fill="white", font=app.f_bold)
        tk.Label(self, text=name, font=app.f_big, fg=P["text"], bg=P["bg"]).pack()
        tk.Label(self, text="テキスト通話の着信", font=app.f_small, fg=P["muted"], bg=P["bg"]).pack()
        row = tk.Frame(self, bg=P["bg"])
        row.pack(pady=px(14))
        tk.Button(row, text="拒否", font=app.f_bold, bg=RED, fg="white", activebackground="#B9362F",
                  activeforeground="white", relief="flat", width=8, cursor="hand2",
                  command=self.decline).pack(side="left", padx=px(8))
        tk.Button(row, text="応答", font=app.f_bold, bg=GREEN, fg="white", activebackground="#236240",
                  activeforeground="white", relief="flat", width=8, cursor="hand2",
                  command=self.accept).pack(side="left", padx=px(8))

        set_titlebar(self, P["dark"])
        self.started = time.time()
        self.lift()
        self.focus_force()
        self._ring()

    def _ring(self):
        if not self.winfo_exists():
            return
        if time.time() - self.started > RING_TIMEOUT:
            self.decline(missed=True)
            return
        beep("ring")
        self.after(1600, self._ring)

    def accept(self):
        self.destroy()
        self.app.accept(self.call)

    def decline(self, missed: bool = False):
        if self.winfo_exists():
            self.destroy()
        self.app.decline(self.call, missed)


# ================================================================ 通話画面

class Card(tk.Canvas):
    """角の丸いカード。大きさが変わるたびに描き直す。"""

    def __init__(self, master, fill: str, on_resize):
        super().__init__(master, bg=P["bg"], highlightthickness=0, bd=0)
        self.fill = fill
        self.on_resize = on_resize
        self.bind("<Configure>", self._redraw)

    def _redraw(self, e):
        self.delete("card")
        rounded_rect(self, 1, 1, e.width - 1, e.height - 1, 26, fill=self.fill, outline="", tags="card")
        self.tag_lower("card")
        self.on_resize(e.width, e.height)


class CallWindow(tk.Toplevel):
    """通話画面。上のカードに相手がいま打っている文字、下のカードに自分の文字。履歴は残さない。"""

    def __init__(self, app: "App", peer_name: str, outgoing: bool = True):
        super().__init__(app.root)
        self.app = app
        self.peer_name = peer_name
        self.call: lan.Call | None = None
        self.talking_since: float | None = None
        self.ended = False
        self._send_job = None
        self.partner_live = ""
        self.partner_seen = 0.0
        self.notice = "呼び出しています…" if outgoing else ""
        px = app.px

        self.title(f"{TITLE} - {peer_name}")
        self.configure(bg=P["bg"])
        self.minsize(px(260), px(220))  # 吹き出しくらいの小さな画面(広げることもできる)
        self.protocol("WM_DELETE_WINDOW", self.close)

        # 上の帯: 通話時間と「切る」
        bar = tk.Frame(self, bg=P["bg"])
        bar.pack(side="top", fill="x", padx=px(10), pady=(px(8), px(2)))
        self.state_lbl = tk.Label(bar, text="呼び出し中…" if outgoing else "", font=app.f_small, fg=P["muted"], bg=P["bg"])
        self.state_lbl.pack(side="left")
        self.hang = tk.Button(bar, text="切る", font=app.f_small, bg=RED, fg="white", relief="flat",
                              activebackground="#B9362F", activeforeground="white", padx=px(12), cursor="hand2",
                              command=self.hang_up)
        self.hang.pack(side="right")

        # 下: リアクションとヒント
        tk.Label(self, text="打った文字は相手に見えます ・ Enter で消す",
                 font=app.f_small, fg=P["hint"], bg=P["bg"]).pack(side="bottom", pady=(0, px(6)))
        rx = tk.Frame(self, bg=P["bg"])
        rx.pack(side="bottom", pady=(px(2), 0))
        self.rx_buttons = []
        for emo in REACTIONS:
            b = tk.Button(rx, text=emo, font=app.f_emoji, relief="flat", bg=P["bg"], fg=P["text"],
                          activebackground=P["hover"], activeforeground=P["text"], disabledforeground=P["disabled"],
                          cursor="hand2", state="disabled", command=lambda e=emo: self.react(e))
            b.pack(side="left", padx=px(1))
            self.rx_buttons.append(b)

        # 2枚のカード(上: 相手 / 下: 自分)。どちらもツルの一声の吹き出しくらいの大きさ
        lh = app.f_input.metrics("linespace")
        cards = tk.Frame(self, bg=P["bg"])
        cards.pack(side="top", fill="both", expand=True, padx=px(10))
        cards.rowconfigure(1, weight=1)  # 画面を広げたときは相手のカードが広がる
        cards.rowconfigure(2, weight=0)
        cards.columnconfigure(0, weight=1)
        tk.Label(cards, text=peer_name, font=app.f_name, fg=P["text"], bg=P["bg"]).grid(row=0, column=0, sticky="w",
                                                                                  pady=(0, px(3)))
        self.them = Card(cards, P["card_them"], self._layout_them)
        self.them.configure(width=px(300), height=lh * 3 + px(30))
        self.them.grid(row=1, column=0, sticky="nsew")
        self.me_card = Card(cards, P["card_me"], self._layout_me)
        self.me_card.configure(width=px(300), height=lh * 2 + px(24))
        self.me_card.grid(row=2, column=0, sticky="nsew", pady=(px(8), 0))

        r = px(11)
        cx = cy = px(8) + r
        self.ring = self.them.create_oval(cx - r - 2, cy - r - 2, cx + r + 2, cy + r + 2, outline=P["ring_idle"], width=2)
        self.them.create_oval(cx - r + 1, cy - r + 1, cx + r - 1, cy + r - 1, fill=avatar_color(peer_name), outline="")
        self.them.create_text(cx, cy, text=peer_name[:1], fill="white", font=app.f_small)
        self.them_text = self.them.create_text(0, 0, text=self.notice, fill=P["muted"], font=app.f_input,
                                               justify="center")

        self.mine = tk.Text(self.me_card, height=2, wrap="char", font=app.f_input, bg=P["card_me"], fg=P["text"],
                            relief="flat", bd=0, highlightthickness=0, insertbackground=P["text"],
                            insertwidth=px(2), state="disabled", spacing1=px(2))
        self.mine.tag_configure("c", justify="center")
        self.mine_win = self.me_card.create_window(0, 0, window=self.mine)
        self.mine.bind("<Return>", self._enter)
        self.mine.bind("<<Modified>>", self._modified)

        set_titlebar(self, P["dark"])
        self._tick()

    # ---------------------------------------------------- 表示

    def _layout_them(self, w, h):
        self.them.coords(self.them_text, w / 2, h / 2)
        self.them.itemconfigure(self.them_text, width=max(50, w - self.app.px(64)))

    def _layout_me(self, w, h):
        lh = self.app.f_input.metrics("linespace") + self.app.px(2)
        self.me_card.coords(self.mine_win, w / 2, h / 2)
        self.me_card.itemconfigure(self.mine_win, width=max(50, w - self.app.px(32)),
                                   height=max(lh, min(h - self.app.px(12), lh * 2 + self.app.px(4))))

    def _show_them(self):
        if self.partner_live:
            self.them.itemconfigure(self.them_text, text=self.partner_live[-300:], fill=P["text"])
        else:
            self.them.itemconfigure(self.them_text, text=self.notice, fill=P["muted"])

    def _tick(self):
        if not self.winfo_exists():
            return
        talking = self.talking_since and not self.ended
        typing = talking and time.time() - self.partner_seen < 3
        self.them.itemconfigure(self.ring, outline=P["ring"] if typing else (P["ring_talk"] if talking else P["ring_idle"]))
        if talking:
            s = int(time.time() - self.talking_since)
            self.state_lbl.configure(text=f"通話中  {s // 60:02d}:{s % 60:02d}", fg=P["ring"])
        self.after(300, self._tick)

    def float_reaction(self, emo: str, mine: bool):
        """リアクションをカードの上にふわっと出す。"""
        cv = self.them  # カードが小さいので、どちらのリアクションも相手のカードの上に出す
        w, h = cv.winfo_width(), cv.winfo_height()
        xr = 0.86 if mine else 0.5
        item = cv.create_text(w * xr, h * 0.85, text=emo, font=self.app.f_react, fill=P["text"])
        steps = 14

        def step(i=0):
            if not cv.winfo_exists():
                return
            if i > steps:
                cv.delete(item)
                return
            cv.coords(item, w * xr, h * (0.85 - 0.6 * i / steps))
            self.after(60, step, i + 1)
        step()

    # ---------------------------------------------------- 状態

    def attach(self, call: lan.Call):
        self.call = call

    def start_talking(self):
        self.talking_since = time.time()
        self.notice = ""
        self._show_them()
        self.mine.configure(state="normal")
        for b in self.rx_buttons:
            b.configure(state="normal")
        self.lift()
        self.mine.focus_force()

    def end(self, reason: str):
        if self.ended:
            return
        self.ended = True
        dur = ""
        if self.talking_since:
            s = int(time.time() - self.talking_since)
            dur = f"\n(通話時間 {s // 60:02d}:{s % 60:02d})"
        self.partner_live = ""
        self.notice = reason + dur
        self._show_them()
        self.mine.configure(state="normal")
        self.mine.delete("1.0", "end")
        self.mine.configure(state="disabled")
        for b in self.rx_buttons:
            b.configure(state="disabled")
        self.hang.configure(text="閉じる", bg=P["closed_btn"], command=self.close)
        self.state_lbl.configure(text="終了", fg=P["muted"])

    # ---------------------------------------------------- 操作

    def _modified(self, _e=None):
        if self.mine.edit_modified():
            self.mine.edit_modified(False)
            self.mine.tag_add("c", "1.0", "end")
            if self._send_job is None and self.call and self.talking_since and not self.ended:
                self._send_job = self.after(40, self._send_live)

    def _send_live(self):
        self._send_job = None
        if self.call and not self.ended:
            self.call.send("text", v=self.mine.get("1.0", "end-1c")[:lan.MAX_TEXT])

    def _enter(self, _e=None):
        self.clear_mine()
        return "break"

    def clear_mine(self):
        """Enter: 自分の文字を消して次の言葉へ(どこにも残さない)。"""
        if not self.ended:
            self.mine.delete("1.0", "end")

    def react(self, emo: str):
        if self.call and not self.ended:
            self.call.send("react", v=emo)
            self.float_reaction(emo, mine=True)

    def on_message(self, msg: dict):
        t = msg.get("t")
        v = msg.get("v") if isinstance(msg.get("v"), str) else ""
        if t == "accept":
            self.start_talking()
        elif t == "text":
            self.partner_live = v
            self.partner_seen = time.time()
            self._show_them()
        elif t == "react" and v in REACTIONS:
            self.float_reaction(v, mine=False)
        elif t == "decline":
            self.end("応答がありませんでした")
        elif t == "busy":
            self.end(f"{self.peer_name} さんはほかの人と通話中でした")
        elif t in ("bye", "lost"):
            self.end(f"{self.peer_name} さんが通話を終了しました" if self.talking_since
                     else "通話がつながりませんでした")

    def hang_up(self):
        if self.call:
            self.call.hang_up()
        self.end("通話を終了しました" if self.talking_since else "呼び出しをやめました")
        self.app.call_finished(self)

    def close(self):
        if not self.ended:
            if self.talking_since and not messagebox.askyesno(TITLE, "通話を終了しますか?", parent=self):
                return
            self.hang_up()
        self.app.call_finished(self)
        self.destroy()


# ================================================================ メイン(相手の一覧)

class App:
    def __init__(self, profile: str | None = None):
        self.scale = dpi_scale()
        os.makedirs(APP_DIR, exist_ok=True)
        self.settings_path = os.path.join(APP_DIR, f"settings{'_' + profile if profile else ''}.json")
        self.settings = {"name": "", "theme": "黒", **self._load_settings()}
        P.update(THEMES.get(self.settings["theme"], THEMES["黒"]))
        self.events: queue.Queue = queue.Queue()
        self.peers: list[dict] = []
        self.call: lan.Call | None = None
        self.call_win: CallWindow | None = None
        self.incoming: IncomingWindow | None = None

        self.root = tk.Tk()
        self.root.title(TITLE)
        self._fonts()
        self.root.geometry(f"{self.px(340)}x{self.px(480)}")
        self.root.minsize(self.px(300), self.px(360))
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)

        if not self.settings["name"]:
            self.root.withdraw()
            self.settings["name"] = self._ask_name() or os.environ.get("USERNAME", "名無し")
            self._save()
            self.root.deiconify()

        self._build()
        set_titlebar(self.root, P["dark"])
        try:
            self.net = lan.Lan(self.settings["name"], lambda k, d: self.events.put((k, d)))
        except OSError as ex:
            messagebox.showerror(TITLE, f"ネットワークの準備ができませんでした。\n{ex}")
            self.root.destroy()
            raise SystemExit(1)
        self._update_me()
        self.root.after(100, self._poll)

    # ---------------------------------------------------- 準備

    def px(self, v: float) -> int:
        return int(v * self.scale)

    def _fonts(self):
        for name in ("TkDefaultFont", "TkTextFont", "TkMenuFont", "TkHeadingFont"):
            tkfont.nametofont(name).configure(family="Yu Gothic UI", size=10)
        self.f_small = tkfont.Font(family="Yu Gothic UI", size=9)
        self.f_body = tkfont.Font(family="Yu Gothic UI", size=11)
        self.f_bold = tkfont.Font(family="Yu Gothic UI", size=11, weight="bold")
        self.f_big = tkfont.Font(family="Yu Gothic UI", size=16, weight="bold")
        self.f_input = tkfont.Font(family="Yu Gothic UI", size=13)
        self.f_live = tkfont.Font(family="Yu Gothic UI", size=15)
        self.f_card = tkfont.Font(family="Yu Gothic UI", size=17)
        self.f_name = tkfont.Font(family="Yu Gothic UI", size=10, weight="bold")
        self.f_emoji = tkfont.Font(family="Segoe UI Emoji", size=15)
        self.f_huge = tkfont.Font(family="Segoe UI Emoji", size=40)
        self.f_react = tkfont.Font(family="Segoe UI Emoji", size=22)
        style = ttk.Style()
        style.theme_use("clam")  # 一覧の色を白/黒に変えられるテーマ
        style.configure("Treeview", rowheight=self.px(30), font=self.f_body, borderwidth=0)

    def _load_settings(self) -> dict:
        try:
            with open(self.settings_path, encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}

    def _save(self):
        try:
            with open(self.settings_path, "w", encoding="utf-8") as f:
                json.dump(self.settings, f, ensure_ascii=False, indent=1)
        except OSError:
            pass

    def _ask_name(self) -> str | None:
        name = simpledialog.askstring(TITLE, "相手に表示する、あなたの名前を入力してください。",
                                      initialvalue=self.settings.get("name", ""), parent=self.root)
        return name.strip()[:32] if name and name.strip() else None

    def _build(self):
        px = self.px
        bar = tk.Menu(self.root)
        s = tk.Menu(bar, tearoff=False)
        self.v_theme = tk.StringVar(value=self.settings["theme"])
        look = tk.Menu(s, tearoff=False)
        for name in THEMES:
            look.add_radiobutton(label=name, value=name, variable=self.v_theme, command=self.set_theme)
        s.add_cascade(label="見た目 (白 / 黒)", menu=look)
        s.add_separator()
        s.add_command(label="名前の変更…", command=self.rename)
        s.add_command(label="IP アドレスで相手を追加…", command=self.add_ip)
        s.add_command(label="自分の IP アドレス", command=self.show_ip)
        s.add_separator()
        s.add_command(label="終了", command=self.quit)
        bar.add_cascade(label="設定", menu=s)
        self.root.configure(menu=bar)

        self.root.configure(bg=P["bg"])
        self._style_list()
        top = tk.Frame(self.root, bg=P["bg"])
        top.pack(fill="x", padx=px(14), pady=(px(12), px(4)))
        self.count = tk.Label(top, text="相手をさがしています…", font=self.f_bold, fg=P["text"], bg=P["bg"])
        self.count.pack(side="left")
        tk.Button(top, text="更新", font=self.f_small, bg=P["head_bg"], fg=P["text"], relief="flat",
                  activebackground=P["hover"], activeforeground=P["text"], padx=px(12), cursor="hand2",
                  command=lambda: self.net.announce()).pack(side="right")

        bottom = tk.Frame(self.root, bg=P["bg"])
        bottom.pack(side="bottom", fill="x", padx=px(14), pady=(px(6), px(12)))
        self.me = tk.Label(bottom, text="", font=self.f_small, fg=P["muted"], bg=P["bg"], cursor="hand2")
        self.me.pack(side="left")
        self.me.bind("<Button-1>", lambda e: self.copy_my_ip())
        tk.Button(bottom, text="IPをコピー", font=self.f_small, bg=P["head_bg"], fg=P["text"], relief="flat",
                  activebackground=P["hover"], activeforeground=P["text"], padx=px(6), cursor="hand2",
                  command=self.copy_my_ip).pack(side="left", padx=(px(6), 0))
        tk.Button(bottom, text="📞 通話する", font=self.f_bold, bg=GREEN, fg="white", relief="flat",
                  activebackground="#236240", activeforeground="white", padx=px(14), pady=px(4),
                  cursor="hand2", command=self.call_selected).pack(side="right")

        self.status = tk.Label(self.root, text="相手を選んで「通話する」(ダブルクリックでも可)",
                               font=self.f_small, fg=P["muted"], bg=P["bg"])
        self.status.pack(side="bottom", anchor="w", padx=px(14))

        lf = tk.Frame(self.root, bg=P["bg"])
        lf.pack(fill="both", expand=True, padx=px(14), pady=px(4))
        self.tree = ttk.Treeview(lf, columns=("name", "ip"), show="headings", selectmode="browse")
        self.tree.heading("name", text="名前")
        self.tree.heading("ip", text="IP アドレス")
        self.tree.column("name", width=px(150))
        self.tree.column("ip", width=px(110), anchor="center")
        tsb = ttk.Scrollbar(lf, command=self.tree.yview)
        self.tree.configure(yscrollcommand=tsb.set)
        tsb.pack(side="right", fill="y")
        self.tree.pack(fill="both", expand=True)
        self.tree.bind("<Double-1>", lambda e: self.call_selected())
        self.tree.bind("<Button-3>", self._peer_menu)
        self.tree.bind("<Control-c>", lambda e: self.copy_peer_ip())
        self.tree.bind("<Control-C>", lambda e: self.copy_peer_ip())

    def _style_list(self):
        style = ttk.Style()
        style.configure("Treeview", background=P["list_bg"], fieldbackground=P["list_bg"], foreground=P["text"])
        style.map("Treeview", background=[("selected", P["list_sel"])], foreground=[("selected", P["text"])])
        style.configure("Treeview.Heading", background=P["head_bg"], foreground=P["text"], relief="flat",
                        font=self.f_small)
        style.map("Treeview.Heading", background=[("active", P["hover"])])
        style.configure("Vertical.TScrollbar", background=P["head_bg"], troughcolor=P["list_bg"],
                        bordercolor=P["list_bg"], arrowcolor=P["muted"])

    def set_theme(self):
        """見た目を白/黒に切りかえる。一覧の画面は作り直し、通話の画面は次の通話から。"""
        name = self.v_theme.get()
        self.settings["theme"] = name
        self._save()
        P.update(THEMES[name])
        self.root.after(10, self._rebuild)  # メニューの中から呼ばれるので、閉じてから作り直す

    def _rebuild(self):
        for w in self.root.winfo_children():
            if not isinstance(w, tk.Toplevel):
                w.destroy()
        self._build()
        self._update_me()
        self._on_peers(self.peers)
        set_titlebar(self.root, P["dark"])
        if self.call_win:
            self.status.configure(text="通話中の画面は、次の通話から新しい見た目になります")

    def _update_me(self):
        ips = lan.local_ipv4s()
        self.me.configure(text=f"自分: {self.settings['name']}" + (f"  ({ips[0]})" if ips else ""))

    # ---------------------------------------------------- 通話の流れ

    def busy(self) -> bool:
        return self.call_win is not None or self.incoming is not None

    def call_peer(self, pid: str):
        if self.busy():
            self.status.configure(text="いまは通話中です")
            return
        peer = next((p for p in self.peers if p["id"] == pid), None)
        if not peer:
            return
        self.call_win = CallWindow(self, peer["name"])
        self.net.dial(peer)
        self._ringback_timeout(self.call_win)

    def _ringback_timeout(self, win: CallWindow):
        def check():
            if win.winfo_exists() and not win.talking_since and not win.ended:
                if win.call:
                    win.call.hang_up()
                win.end("応答がありませんでした")
                self.call_finished(win)
        self.root.after(RING_TIMEOUT * 1000 + 2000, check)

    def call_selected(self):
        sel = self.tree.selection()
        if not sel:
            self.status.configure(text="先に一覧から相手を選んでください")
            return
        self.call_peer(sel[0])

    def accept(self, call: lan.Call):
        self.incoming = None
        if call.closed:
            self.status.configure(text=f"{call.peer['name']} さんの呼び出しは終わっていました")
            self.call = None
            return
        call.send("accept")
        self.call_win = CallWindow(self, call.peer["name"], outgoing=False)
        self.call_win.attach(call)
        self.call_win.start_talking()

    def decline(self, call: lan.Call, missed: bool):
        self.incoming = None
        call.send("decline")
        call.hang_up()
        if self.call is call:
            self.call = None
        if missed:
            self.status.configure(text=f"{call.peer['name']} さんから不在着信がありました")

    def call_finished(self, win: CallWindow):
        if self.call_win is win:
            self.call_win = None
            self.call = None

    def _poll(self):
        try:
            while True:
                kind, d = self.events.get_nowait()
                if kind == "peers":
                    self._on_peers(d)
                elif kind == "invite":
                    self._on_invite(d)
                elif kind == "dialed":
                    self._on_dialed(d)
                elif kind == "dial_failed":
                    if self.call_win and not self.call_win.call:
                        self.call_win.end(f"{d} さんにつながりませんでした")
                        self.call_finished(self.call_win)
                elif kind == "call":
                    call, msg = d
                    self._on_call_msg(call, msg)
        except queue.Empty:
            pass
        self.root.after(30, self._poll)

    def _on_invite(self, call: lan.Call):
        if self.busy():
            call.send("busy")
            call.hang_up()
            self.status.configure(text=f"通話中に {call.peer['name']} さんから着信がありました")
            return
        self.call = call
        self.incoming = IncomingWindow(self, call)

    def _on_dialed(self, call: lan.Call):
        win = self.call_win
        if not win or win.ended:  # 呼び出し中にやめていた
            call.hang_up()
            return
        self.call = call
        win.attach(call)

    def _on_call_msg(self, call: lan.Call, msg: dict):
        if call is not self.call:
            return
        if self.incoming is not None and msg.get("t") in ("bye", "lost"):  # 応答する前に相手がやめた
            name = call.peer["name"]
            self.incoming.destroy()
            self.incoming = None
            self.call = None
            self.status.configure(text=f"{name} さんから不在着信がありました")
            return
        win = self.call_win
        if win:
            win.on_message(msg)
            if win.ended:
                self.call_finished(win)

    def _on_peers(self, peers: list[dict]):
        sel = self.tree.selection()
        self.peers = peers
        self.tree.delete(*self.tree.get_children())
        for p in peers:
            self.tree.insert("", "end", iid=p["id"], values=(p["name"], p["ip"]))
        if sel and self.tree.exists(sel[0]):
            self.tree.selection_set(sel[0])
        self.count.configure(text=f"オンライン {len(peers)}人" if peers else "相手をさがしています…")

    # ---------------------------------------------------- 設定

    def rename(self):
        name = self._ask_name()
        if name:
            self.settings["name"] = name
            self._save()
            self.net.rename(name)
            self._update_me()

    def add_ip(self):
        ip = simpledialog.askstring(TITLE, "相手の IP アドレスを入力してください(例: 192.168.1.23)。\n"
                                           "ブロードキャストが届かないネットワーク用です。", parent=self.root)
        if not ip:
            return
        try:
            self.net.add_ip(ip.strip())
            self.status.configure(text=f"{ip.strip()} に呼びかけました")
        except OSError:
            messagebox.showwarning(TITLE, "IP アドレスの形が正しくありません。", parent=self.root)

    # ---------------------------------------------------- IP アドレスのコピー

    def copy(self, text: str, what: str):
        self.root.clipboard_clear()
        self.root.clipboard_append(text)
        self.root.update()  # ウィンドウを閉じてもクリップボードに残るように
        self.status.configure(text=f"{what} をコピーしました: {text}")

    def copy_my_ip(self):
        ips = lan.local_ipv4s()
        if ips:
            self.copy(ips[0], "自分の IP アドレス")
        else:
            self.status.configure(text="IP アドレスがわかりませんでした")

    def copy_peer_ip(self):
        sel = self.tree.selection()
        p = next((p for p in self.peers if sel and p["id"] == sel[0]), None)
        if p:
            self.copy(p["ip"], f"{p['name']} さんの IP アドレス")

    def _peer_menu(self, e):
        row = self.tree.identify_row(e.y)
        if not row:
            return
        self.tree.selection_set(row)
        m = tk.Menu(self.root, tearoff=False)
        m.add_command(label="📞 通話する", command=self.call_selected)
        m.add_command(label="IP アドレスをコピー", command=self.copy_peer_ip)
        try:
            m.tk_popup(e.x_root, e.y_root)
        finally:
            m.grab_release()

    def show_ip(self):
        """自分の IP アドレスを、選んだりコピーしたりできる形で見せる。"""
        ips = lan.local_ipv4s()
        px = self.px
        w = tk.Toplevel(self.root)
        w.title("自分の IP アドレス")
        w.configure(bg=P["bg"])
        w.resizable(False, False)
        w.transient(self.root)
        tk.Label(w, text="このパソコンの IP アドレス", font=self.f_bold, fg=P["text"], bg=P["bg"]).pack(
            anchor="w", padx=px(16), pady=(px(14), px(6)))
        if not ips:
            tk.Label(w, text="わかりませんでした", font=self.f_body, fg=P["muted"], bg=P["bg"]).pack(padx=px(16))
        for ip in ips:
            row = tk.Frame(w, bg=P["bg"])
            row.pack(fill="x", padx=px(16), pady=px(3))
            v = tk.StringVar(value=ip)
            ent = tk.Entry(row, textvariable=v, state="readonly", font=self.f_input, width=16, relief="flat",
                           readonlybackground=P["list_bg"], fg=P["text"], highlightthickness=1,
                           highlightbackground=P["ring_idle"])
            ent.pack(side="left", ipady=px(3))
            tk.Button(row, text="コピー", font=self.f_small, bg=GREEN, fg="white", relief="flat",
                      activebackground="#236240", activeforeground="white", padx=px(10), cursor="hand2",
                      command=lambda i=ip: (self.copy(i, "自分の IP アドレス"), w.destroy())).pack(
                side="left", padx=(px(8), 0), fill="y")
        tk.Label(w, text="相手に伝えて「設定 → IP アドレスで相手を追加…」に貼り付けてもらいます",
                 font=self.f_small, fg=P["muted"], bg=P["bg"]).pack(anchor="w", padx=px(16), pady=(px(8), px(14)))
        set_titlebar(w, P["dark"])
        w.focus_force()

    def on_close(self):
        ans = messagebox.askyesnocancel(
            TITLE, "ひとこえ通話を終了しますか?\n終了すると着信を受けられなくなります。\n\n"
                   "「いいえ」を押すと最小化して、着信を待ちます。", parent=self.root)
        if ans is True:
            self.quit()
        elif ans is False:
            self.root.iconify()

    def quit(self):
        if self.call:
            self.call.hang_up()
        self.net.stop()
        self.root.destroy()

    def run(self):
        self.root.mainloop()


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--profile", help="1台で2人分ためすとき用(設定を分ける)")
    args = ap.parse_args()
    if args.profile or single_instance():
        App(args.profile).run()
