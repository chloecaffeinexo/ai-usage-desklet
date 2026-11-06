#!/usr/bin/env python3
"""AI Usage for XFCE: one GTK 3 desktop card drawing the poller's state file.

This is the XFCE companion of the AI Usage Cinnamon desklet. It shows one
glass card with your Claude, ChatGPT, OpenCode Go, Mistral and OpenRouter
usage bars, reset countdowns, plan pills, renewal dates and credit balances,
reading the same state file the desklet reads:

    ~/.cache/ai-usage-desklet/state.json

That file is written by the Python poller (see this repository's poller/ and
the README). No credential is ever read here; this file only ever sees the
summary the poller writes.

Run:      python3 ai-usage.py
Preview:  python3 ai-usage.py --preview OUTDIR   (renders the card to PNG offline)
Config:   ~/.config/ai-usage-xfce/config.json (created on first run, saved when
          you drag the card)
"""

import json
import math
import os
import re
import sys
import time
from datetime import datetime

import gi
gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
from gi.repository import Gdk, Gio, GLib, Gtk, Pango  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
HOME = os.path.expanduser("~")
CONFIG_DIR = os.path.join(GLib.get_user_config_dir(), "ai-usage-xfce")
CONFIG_PATH = os.path.join(CONFIG_DIR, "config.json")

DEFAULT_CONFIG = {
    "scale": "auto",          # "auto" follows the desktop DPI, or a number such as 1.25
    "window_mode": "below",   # "below", "desktop" or "dock"
    "card-width": 300,
    "widgets": {
        "ai-usage": {"enabled": True, "x": 80, "y": 80},
    },
}


def load_config(path=CONFIG_PATH):
    config = json.loads(json.dumps(DEFAULT_CONFIG))
    try:
        with open(path, encoding="utf-8") as handle:
            stored = json.load(handle)
    except (OSError, ValueError):
        return config
    for key, value in stored.items():
        if key == "widgets" and isinstance(value, dict):
            for name, opts in value.items():
                if isinstance(opts, dict):
                    config["widgets"].setdefault(name, {}).update(opts)
        else:
            config[key] = value
    return config


def save_config(config, path=CONFIG_PATH):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as handle:
        json.dump(config, handle, indent=2)
        handle.write("\n")
    os.replace(tmp, path)


def read_text(path):
    try:
        with open(path, "rb") as handle:
            return handle.read().decode("latin-1")
    except OSError:
        return None


def read_json(path):
    text = read_text(path)
    if text is None:
        return None
    try:
        return json.loads(text.encode("latin-1").decode("utf-8", "replace"))
    except ValueError:
        return None


def num(value, default=0.0):
    try:
        result = float(value)
    except (TypeError, ValueError):
        return default
    return result if math.isfinite(result) else default


def label(text, *classes, xalign=0.0, ellipsize=False):
    widget = Gtk.Label(label=str(text))
    widget.set_xalign(xalign)
    if ellipsize:
        widget.set_ellipsize(Pango.EllipsizeMode.END)
    ctx = widget.get_style_context()
    for cls in classes:
        for part in cls.split():
            ctx.add_class(part)
    return widget


def add_classes(widget, *classes):
    ctx = widget.get_style_context()
    for cls in classes:
        for part in cls.split():
            ctx.add_class(part)
    return widget


def row(left, right, width=None, spacing=8):
    box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=spacing)
    if width:
        box.set_size_request(width, -1)
    box.pack_start(left, False, False, 0)
    box.pack_end(right, False, False, 0)
    return box


def format_duration(seconds):
    remaining = max(0, int(num(seconds)))
    if remaining < 60:
        return f"{remaining}s"
    if remaining < 3600:
        return f"{remaining // 60}m"
    if remaining < 86400:
        return f"{remaining // 3600}h {(remaining % 3600) // 60}m"
    return f"{remaining // 86400}d {(remaining % 86400) // 3600}h"


def format_age(fetched_at, now):
    stamp = num(fetched_at)
    if stamp <= 0:
        return "never updated"
    age = max(0, int(now - stamp))
    if age < 60:
        return f"updated {age}s ago"
    if age < 3600:
        return f"updated {age // 60}m ago"
    if age < 86400:
        return f"updated {age // 3600}h ago"
    return f"updated {age // 86400}d ago"


def absolute_time(epoch, twelve_hour=False):
    epoch = num(epoch)
    if epoch <= 0:
        return "unknown time"
    when = datetime.fromtimestamp(round(epoch))
    same_day = when.date() == datetime.now().date()
    if same_day:
        text = when.strftime("%I:%M %p" if twelve_hour else "%H:%M")
    else:
        text = when.strftime("%a %e %b %I:%M %p" if twelve_hour else "%a %e %b %H:%M")
    text = re.sub(r"\s+", " ", text).strip()
    if twelve_hour:
        text = re.sub(r"(^| )0(\d:\d\d)", r"\1\2", text)
    return text


class Meter(Gtk.DrawingArea):
    """Thin rounded bar in the theme's colours: accent purple when normal,
    Orchis amber and red for warning and critical, grey when stale."""

    FILLS = {
        "normal": ((0xCE, 0x93, 0xD8), (0xBA, 0x68, 0xC8)),
        "warning": ((0xFD, 0xD8, 0x35), (0xFB, 0xC0, 0x2D)),
        "critical": ((0xEF, 0x73, 0x6B), (0xF4, 0x43, 0x36)),
        "good": ((0xA5, 0xD6, 0xA7), (0x81, 0xC9, 0x95)),
        "stale": ((0x9E, 0x9E, 0x9E), (0x75, 0x75, 0x75)),
    }

    def __init__(self, width, percent, severity, stale, light, scale):
        super().__init__()
        self.height = max(4, round(5 * scale))
        self.set_size_request(width, self.height)
        self.set_value(percent, severity, stale)
        self.connect("draw", self._draw)

    def set_value(self, percent, severity="normal", stale=False):
        self.percent = max(0.0, min(100.0, percent))
        self.kind = "stale" if stale else (severity if severity in self.FILLS else "normal")
        self.queue_draw()

    @staticmethod
    def _rounded(cr, x, y, w, h, r):
        r = min(r, h / 2, w / 2)
        cr.new_sub_path()
        cr.arc(x + w - r, y + r, r, -math.pi / 2, 0)
        cr.arc(x + w - r, y + h - r, r, 0, math.pi / 2)
        cr.arc(x + r, y + h - r, r, math.pi / 2, math.pi)
        cr.arc(x + r, y + r, r, math.pi, 3 * math.pi / 2)
        cr.close_path()

    def _draw(self, widget, cr):
        import cairo
        width = widget.get_allocated_width()
        h = self.height
        self._rounded(cr, 0, 0, width, h, h / 2)
        cr.set_source_rgba(1, 1, 1, 0.10)
        cr.fill()
        if self.percent <= 0:
            return False
        fill_w = max(h, round(width * self.percent / 100))
        left, right = self.FILLS[self.kind]
        grad = cairo.LinearGradient(0, 0, fill_w, 0)
        grad.add_color_stop_rgb(0, *(c / 255 for c in left))
        grad.add_color_stop_rgb(1, *(c / 255 for c in right))
        self._rounded(cr, 0, 0, fill_w, h, h / 2)
        cr.set_source(grad)
        cr.fill()
        return False


class Desklet:
    """One borderless desktop window. The card builds `content()` and says when
    it refreshes."""

    name = "ai-usage"
    refresh_seconds = 60

    def __init__(self, app, opts, preview=False):
        self.app = app
        self.opts = opts
        self.scale = app.scale
        self.preview = preview
        self._timer = None
        self._content = None
        if preview:
            self.window = Gtk.OffscreenWindow()
        else:
            self.window = Gtk.Window(type=Gtk.WindowType.TOPLEVEL)
            self._setup_desktop_window()
        add_classes(self.window, "xd-window")
        if not app.composited:
            add_classes(self.window, "xd-opaque")
        visual = self.window.get_screen().get_rgba_visual()
        if visual is not None and (app.composited or preview):
            self.window.set_visual(visual)
        self.window.set_app_paintable(True)
        self.window.connect("draw", self._clear)
        self.holder = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.window.add(self.holder)

    # --- window behaviour -----------------------------------------------

    def _setup_desktop_window(self):
        win = self.window
        win.set_title("Desklet: ai-usage")
        win.set_wmclass("ai-usage-xfce", "AIUsageXFCE")
        win.set_decorated(False)
        win.set_resizable(False)
        win.set_skip_taskbar_hint(True)
        win.set_skip_pager_hint(True)
        win.set_accept_focus(False)
        win.set_focus_on_map(False)
        mode = self.app.config.get("window_mode", "below")
        if mode == "desktop":
            win.set_type_hint(Gdk.WindowTypeHint.DESKTOP)
        elif mode == "dock":
            win.set_type_hint(Gdk.WindowTypeHint.DOCK)
        else:
            win.set_type_hint(Gdk.WindowTypeHint.UTILITY)
        win.set_keep_below(True)
        win.stick()
        win.move(int(num(self.opts.get("x"), 0)), int(num(self.opts.get("y"), 0)))
        win.add_events(Gdk.EventMask.BUTTON_PRESS_MASK
                       | Gdk.EventMask.BUTTON_RELEASE_MASK
                       | Gdk.EventMask.POINTER_MOTION_MASK)
        self._press = None
        self.dragged = False
        win.connect("button-press-event", self._on_press)
        win.connect("motion-notify-event", self._on_motion)
        win.connect("configure-event", self._on_configure)
        win.connect("map-event", lambda *_: win.set_keep_below(True))

    def _clear(self, widget, cr):
        import cairo
        cr.set_source_rgba(0, 0, 0, 0)
        cr.set_operator(cairo.OPERATOR_SOURCE)
        cr.paint()
        cr.set_operator(cairo.OPERATOR_OVER)
        return False

    def _on_press(self, widget, event):
        if event.button == 1:
            self._press = (event.x_root, event.y_root, event.time)
            self.dragged = False
        return False

    def _on_motion(self, widget, event):
        if not self._press or not (event.state & Gdk.ModifierType.BUTTON1_MASK):
            return False
        x0, y0, stamp = self._press
        if abs(event.x_root - x0) + abs(event.y_root - y0) > 8:
            self.dragged = True
            self._press = None
            self.window.begin_move_drag(1, int(x0), int(y0), stamp)
        return False

    def _on_configure(self, widget, event):
        x, y = self.window.get_position()
        if (x, y) != (self.opts.get("x"), self.opts.get("y")):
            self.opts["x"], self.opts["y"] = x, y
            self.app.schedule_save()
        return False

    def clickable(self, widget, callback):
        """Wrap a widget so a plain click (not a drag) runs callback."""
        box = Gtk.EventBox()
        box.set_visible_window(False)
        box.add(widget)
        box.add_events(Gdk.EventMask.BUTTON_RELEASE_MASK)

        def released(_w, event):
            if event.button == 1 and not getattr(self, "dragged", False):
                callback()
            return False
        box.connect("button-release-event", released)
        return box

    # --- refresh loop ---------------------------------------------------

    def start(self):
        self.refresh()
        if not self.preview:
            self.window.show_all()
            self._timer = GLib.timeout_add(int(self.refresh_seconds * 1000), self._tick)

    def _tick(self):
        self.refresh()
        return True

    def refresh(self):
        try:
            content = self.content()
        except Exception as error:  # never let a render fault kill the loop
            print(f"{self.name}: {error}", file=sys.stderr)
            content = self.error_view("Data unavailable")
        if self._content is not None:
            self.holder.remove(self._content)
            self._content.destroy()
        self._content = content
        self.holder.pack_start(content, False, False, 0)
        self.holder.show_all()
        if not self.preview:
            self.window.resize(1, 1)  # shrink to fit when content gets smaller

    def px(self, value):
        return round(value * self.scale)

    def card_width(self):
        return self.px(min(480, max(220, num(self.app.config.get("card-width"), 300))))

    def card(self, style="dark", extra=""):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=self.px(4))
        add_classes(box, "xd-card", "xd-card-solid" if style == "solid" else "", extra)
        return box

    def root(self, title=None, density="compact"):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        add_classes(box, "xd-root")
        return box

    def header(self, title, meta=None):
        """Accent dot, spaced capitals, optional meta text on the right."""
        head = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=self.px(5))
        head.pack_start(label("●", "xd-head-dot"), False, False, 0)
        head.pack_start(label(title.upper(), "xd-head"), False, False, 0)
        if isinstance(meta, Gtk.Widget):
            head.pack_end(meta, False, False, 0)
        elif meta:
            head.pack_end(label(meta, "xd-meta"), False, False, 0)
        head.set_margin_bottom(self.px(2))
        return head

    def panel(self, title, meta=None, extra=""):
        """Root plus one card with the shared header. Returns (root, card, inner width)."""
        root = self.root()
        card = self.card(self.opts.get("card-style", "dark"), extra)
        width = self.card_width()
        card.set_size_request(width, -1)
        card.pack_start(self.header(title, meta), False, False, 0)
        root.pack_start(card, False, False, 0)
        return root, card, width - self.px(30)

    def rule(self):
        sep = Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL)
        add_classes(sep, "xd-rule")
        return sep

    def error_view(self, message):
        root, card, _ = self.panel(self.title_text() or self.name)
        card.pack_start(label(message, "xd-muted"), False, False, 0)
        return root

    def title_text(self):
        return None

    def content(self):
        raise NotImplementedError


# ---------------------------------------------------------------------------
# The AI Usage card
# ---------------------------------------------------------------------------

class AIUsage(Desklet):
    name = "ai-usage"
    refresh_seconds = 30
    STATE_PATH = os.path.join(HOME, ".cache", "ai-usage-desklet", "state.json")
    PROVIDER_URIS = {
        "claude": "https://claude.ai/settings/usage",
        "chatgpt": "https://chatgpt.com/#settings/Account",
        "opencode": "https://opencode.ai/auth",
        "mistral": "https://admin.mistral.ai/organization/usage",
        "openrouter": "https://openrouter.ai/credits",
    }
    PROVIDER_SIGNIN = {
        "claude": "In a terminal: claude, then /login",
        "chatgpt": "Open a terminal, type  codex login  and press enter",
        "opencode": "Add your OpenCode Go key to  ~/.local/share/ai-usage-desklet/opencode.env",
        "mistral": "In a terminal: vibe, then sign in",
        "openrouter": "Add your OpenRouter key to  ~/.local/share/ai-usage-desklet/openrouter.env",
    }

    def __init__(self, app, opts, preview=False):
        super().__init__(app, opts, preview)
        self._countdowns = []
        self._footers = []
        self._rendered_key = None
        if not preview:
            GLib.timeout_add_seconds(1, self._tick_countdowns)

    def title_text(self):
        return "AI Usage"

    @property
    def twelve(self):
        return self.opts.get("time-format", "24h") == "12h"

    def content_width(self):
        return self.px(min(480, max(260, num(self.opts.get("content-width"), 360))))

    def refresh(self):
        # Rebuild only when something visible changed, so an open tooltip is
        # not torn down every 30 seconds.
        state = read_json(self.STATE_PATH)
        if state and state.get("schema") == 1 and isinstance(state.get("providers"), list):
            key = json.dumps([dict(p, fetched_at=0, next_attempt_at=0) if isinstance(p, dict) else p
                              for p in state["providers"]], sort_keys=True)
            if key == self._rendered_key and self._content is not None:
                by_id = {p.get("id"): p for p in state["providers"] if isinstance(p, dict)}
                for entry in self._footers:
                    provider = by_id.get(entry["id"])
                    if provider:
                        entry["fetched"] = num(provider.get("fetched_at"))
                return
            self._rendered_key = key
        else:
            self._rendered_key = None
        self._state = state
        super().refresh()

    ORDER = ["claude", "chatgpt", "opencode", "mistral", "openrouter"]

    def content(self):
        self._countdowns = []
        self._footers = []
        state = self._state
        if not state or state.get("schema") != 1 or not isinstance(state.get("providers"), list):
            return self.error_view("Usage data unavailable")
        providers = [p for p in state["providers"]
                     if isinstance(p, dict) and self.opts.get(f"show-{p.get('id')}", True)]
        rank = {pid: i for i, pid in enumerate(self.ORDER)}
        providers.sort(key=lambda p: rank.get(p.get("id"), len(rank)))
        root, card, inner = self.panel("AI usage")
        now = time.time()
        if not providers:
            card.pack_start(label("No providers shown", "xd-muted"), False, False, 0)
        for index, provider in enumerate(providers):
            if index:
                card.pack_start(self.rule(), False, False, self.px(2))
            card.pack_start(self.provider_section(provider, inner, now), False, False, 0)
        return root

    def bars_for(self, provider):
        bars = provider.get("bars") if isinstance(provider.get("bars"), list) else []
        bars = [b for b in bars if isinstance(b, dict)]
        if not self.opts.get("show-inactive", True):
            bars = [b for b in bars if b.get("active") is not False]
        return bars

    def renewal_text(self, provider):
        """The plan's clock-over date, only where the poller has a real one.

        The poller publishes renews_at only from a field the provider actually
        returns (ChatGPT's token claims, OpenCode's monthly window, Mistral's
        budget reset); Claude and OpenRouter publish nothing, so those cards
        show no date rather than a computed guess.
        """
        renews = num(provider.get("renews_at"))
        if renews <= 0:
            return ""
        when = datetime.fromtimestamp(round(renews))
        today = datetime.now()
        text = when.strftime("%e %b %Y") if when.year != today.year else when.strftime("%e %b")
        text = text.strip()
        period = provider.get("renews_period")
        return f"renews {text}, {period}" if period else f"renews {text}"

    def provider_section(self, provider, inner, now):
        stale = provider.get("stale") is True or provider.get("ok") is False
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=self.px(3))
        head = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
        head.set_size_request(inner, -1)
        head.pack_start(label(provider.get("label") or provider.get("id") or "Provider", "xd-name"),
                        False, False, 0)
        plan = provider.get("plan")
        if plan and plan != "Unknown":
            head.pack_start(label(plan, "xd-pill xd-pill-plan"), False, False, 0)
        renewal = self.renewal_text(provider)
        if renewal:
            head.pack_start(label(renewal, "xd-renews", ellipsize=True), False, False, 0)
        bars = self.bars_for(provider)
        if provider.get("auth_state") == "expired" or (provider.get("ok") is False and not bars):
            head.pack_end(label("sign in needed" if provider.get("auth_state") == "expired"
                                else (provider.get("error_short") or "unavailable"),
                                "xd-pill xd-pill-error"), False, False, 0)
        else:
            footer = label(format_age(provider.get("fetched_at"), now).replace("updated ", ""),
                           "xd-small")
            self._footers.append({"label": footer, "fetched": num(provider.get("fetched_at")),
                                  "id": provider.get("id"), "short": True})
            head.pack_end(footer, False, False, 0)
        box.pack_start(head, False, False, 0)

        body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=self.px(5))
        if stale:
            add_classes(body, "xd-stale")
        if not bars and provider.get("auth_state") == "expired":
            body.pack_start(label(self.PROVIDER_SIGNIN.get(provider.get("id"), "Sign in again"),
                                  "xd-small", ellipsize=True), False, False, 0)
        elif not bars:
            body.pack_start(label("Usage unavailable", "xd-small"), False, False, 0)
        for bar in bars:
            body.pack_start(self.bar_view(bar, inner, stale, now), False, False, 0)
        if self.opts.get("show-credits", True) and isinstance(provider.get("credits"), dict):
            credits = provider["credits"]
            sev = self.severity(credits.get("severity"))
            body.pack_start(row(label("Credits", "xd-label"),
                                label(self.credits_text(credits), f"xd-value xd-sev-{sev}"), inner),
                            False, False, 0)
        box.pack_start(body, False, False, 0)
        box.set_tooltip_text(self.help_text(provider, bars, now) if self.opts.get("show-help")
                             else self.tooltip_text(provider, bars))
        return self.clickable(box, lambda pid=provider.get("id"): self.open_provider(pid))

    @staticmethod
    def severity(value):
        return value if value in ("normal", "warning", "critical") else "normal"

    def bar_view(self, bar, track, stale, now):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=self.px(3))
        pct = max(0.0, min(100.0, num(bar.get("percent"))))
        sev = self.severity(bar.get("severity"))
        top = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=self.px(6))
        top.set_size_request(track, -1)
        top.pack_start(label(bar.get("label") or "Limit", "xd-label"), False, False, 0)
        pill = label("resetting", "xd-pill xd-pill-reset")
        pill.set_no_show_all(True)
        top.pack_start(pill, False, False, 0)
        top.pack_end(label(f"{round(pct)}%", f"xd-value xd-sev-{sev}"), False, False, 0)
        countdown = label("", "xd-small")
        top.pack_end(countdown, False, False, 0)
        box.pack_start(top, False, False, 0)
        box.pack_start(Meter(track, pct, sev, stale, False, self.scale), False, False, 0)
        entry = {"label": countdown, "pill": pill, "resets": num(bar.get("resets_at"), -1),
                 "no_reset": bar.get("resets_at", 0) is None}
        self._countdowns.append(entry)
        self.update_countdown(entry, now)
        return box

    def update_countdown(self, entry, now):
        reset = entry["resets"]
        valid = reset > 0
        resetting = valid and now >= reset
        entry["pill"].set_visible(resetting)
        if entry["no_reset"]:
            entry["label"].set_text("")
        elif not valid:
            entry["label"].set_text("reset unavailable")
        elif resetting:
            entry["label"].set_text(" ")
        else:
            entry["label"].set_text(f"resets in {format_duration(reset - now)}")

    def _tick_countdowns(self):
        now = time.time()
        for entry in self._countdowns:
            self.update_countdown(entry, now)
        for entry in self._footers:
            text = format_age(entry["fetched"], now)
            entry["label"].set_text(text.replace("updated ", "") if entry.get("short") else text)
        return True

    def credits_text(self, credits):
        if not isinstance(credits, dict):
            return "unavailable"
        if credits.get("short_text") is not None:
            return str(credits["short_text"])
        if credits.get("kind") == "money":
            return str(credits.get("used_text") or "—") if credits.get("enabled") else "off"
        if credits.get("unlimited"):
            return "unlimited"
        return str(credits.get("balance_text", "0"))

    def credits_tooltip(self, credits):
        if not isinstance(credits, dict):
            return "Credits: unavailable"
        if credits.get("kind") == "money":
            used = credits.get("used_text") or "—"
            limit = credits.get("limit_text")
            return f"Credits: {used} / {limit}" if limit else f"Credits: {used}"
        if credits.get("unlimited"):
            return "Credits: unlimited"
        return f"Credits: {credits.get('balance_text', '0')}"

    def tooltip_text(self, provider, bars):
        lines = []
        renewal = self.renewal_text(provider)
        if renewal:
            when = renewal[len("renews "):]
            if ", " in when:
                lines.append(f"Plan clocks over {when}")
            else:
                lines.append(f"Plan clocks over {when} (period unknown)")
        for bar in bars:
            name = bar.get("label") or "Limit"
            if bar.get("resets_at", 0) is None:
                lines.append(f"{name}: no reset time published")
            else:
                lines.append(f"{name} resets {absolute_time(bar.get('resets_at'), self.twelve)}")
        lines.append(self.credits_tooltip(provider.get("credits")))
        if provider.get("error"):
            lines.append(f"Error: {provider['error']}")
        return "\n".join(lines)

    def next_attempt(self, provider, now):
        nxt = num(provider.get("next_attempt_at"))
        if nxt <= 0:
            return ""
        when = absolute_time(nxt, self.twelve)
        return f"{when}, {format_duration(nxt - now)} away" if nxt > now else when

    def help_text(self, provider, bars, now):
        name = provider.get("label") or provider.get("id") or "Provider"
        plan = provider.get("plan") if provider.get("plan") not in (None, "", "Unknown") else "plan unknown"
        fetched = absolute_time(provider.get("fetched_at"), self.twelve)
        signin = self.PROVIDER_SIGNIN.get(provider.get("id"), "Sign in to the provider again")
        nxt = self.next_attempt(provider, now)
        next_line = f"Next attempt: {nxt}." if nxt else "Next attempt: as soon as the poller runs."
        err = str(provider.get("error_short") or "").lower()
        resetting = any(0 < num(b.get("resets_at"), -1) <= now for b in bars)
        if provider.get("auth_state") == "expired":
            lines = [f"{name}: the sign-in has expired, so these figures are",
                     f"frozen at {fetched} and will not move.", "",
                     f"Fix it: {signin}", f"The card then recovers on its own. {next_line}"]
        elif provider.get("ok") is False and "rate" in err:
            lines = [f"{name}: the provider is throttling the usage endpoint,",
                     "which is normal and needs nothing from you.", "",
                     f"Showing the last good figures, from {fetched}.", next_line]
        elif provider.get("ok") is False:
            lines = [f"{name}: could not reach the provider ({provider.get('error_short') or 'reason unknown'}).",
                     "", f"Showing the last good figures, from {fetched}.", next_line,
                     "If this persists, check the network first, then run:",
                     "python3 ~/.local/share/ai-usage-desklet/poller.py --once --print"]
        elif resetting:
            lines = [f"{name} · {plan}: a limit window has just rolled over.", "",
                     "Holding the previous figure rather than guessing at zero,",
                     f"until the next successful check. {next_line}"]
        elif provider.get("plan_source") not in ("profile", "api"):
            lines = [f"{name}: the plan is shown as {plan}, but that was read",
                     "from the sign-in file on this computer rather than from",
                     "the account, so it can lag a plan change by weeks.", "",
                     f"To refresh it: {signin}", "",
                     "The percentages are unaffected. They are measured by the",
                     "provider against your real plan either way."]
        else:
            lines = [f"{name} · {plan}, {format_age(provider.get('fetched_at'), now)}.",
                     "Plan confirmed live against the account."]
            for bar in bars:
                if bar.get("resets_at", 0) is None:
                    lines.append(f"{bar.get('label') or 'Limit'} has no reset time published.")
                else:
                    lines.append(f"{bar.get('label') or 'Limit'} resets "
                                 f"{absolute_time(bar.get('resets_at'), self.twelve)}.")
            lines.append(next_line)
        lines += ["", "Turn bars, credits and cards on or off by editing:"]
        lines += ["~/.config/ai-usage-xfce/config.json"]
        return "\n".join(lines)

    def open_provider(self, provider_id):
        uri = self.PROVIDER_URIS.get(provider_id)
        if uri:
            try:
                Gio.AppInfo.launch_default_for_uri(uri, None)
            except GLib.Error as error:
                print(f"ai-usage: browser launch: {error.message}", file=sys.stderr)

    def open_config(self):
        try:
            Gio.AppInfo.launch_default_for_uri(GLib.filename_to_uri(CONFIG_PATH, None), None)
        except GLib.Error as error:
            print(f"ai-usage: settings launch: {error.message}", file=sys.stderr)


# ---------------------------------------------------------------------------
# Application
# ---------------------------------------------------------------------------

class App:
    def __init__(self, config, preview=False):
        self.config = config
        self.preview = preview
        screen = Gdk.Screen.get_default()
        self.composited = screen.is_composited() if not preview else True
        scale = config.get("scale", "auto")
        if scale == "auto":
            resolution = screen.get_resolution()
            scale = resolution / 96 if resolution and resolution > 0 else 1.0
        self.scale = max(0.5, min(4.0, num(scale, 1.0)))
        css = Gtk.CssProvider()
        css.load_from_path(os.path.join(HERE, "style.css"))
        Gtk.StyleContext.add_provider_for_screen(screen, css,
                                                 Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        self._save_id = None
        self.desklets = []
        opts = config["widgets"].setdefault("ai-usage", {})
        if opts.get("enabled", True):
            self.desklets.append(AIUsage(self, opts, preview))

    def schedule_save(self):
        if self.preview:
            return
        if self._save_id:
            GLib.source_remove(self._save_id)
        self._save_id = GLib.timeout_add_seconds(2, self._save)

    def _save(self):
        self._save_id = None
        try:
            save_config(self.config)
        except OSError as error:
            print(f"could not save positions: {error}", file=sys.stderr)
        return False

    def run(self):
        for desklet in self.desklets:
            desklet.start()
        Gtk.main()

    def render_previews(self, outdir):
        os.makedirs(outdir, exist_ok=True)
        written = []
        for desklet in self.desklets:
            desklet.refresh()
            desklet.window.show_all()
            for _ in range(20):
                while Gtk.events_pending():
                    Gtk.main_iteration_do(False)
                time.sleep(0.02)
            pixbuf = desklet.window.get_pixbuf()
            path = os.path.join(outdir, f"{desklet.name}.png")
            if pixbuf is not None:
                pixbuf.savev(path, "png", [], [])
                written.append(path)
        return written


def single_instance():
    """Refuse a second copy, so a restart from autostart never doubles the card."""
    lock_dir = os.path.join(GLib.get_user_runtime_dir(), "ai-usage-xfce")
    os.makedirs(lock_dir, exist_ok=True)
    import fcntl
    handle = open(os.path.join(lock_dir, "lock"), "w")
    try:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        return None
    return handle


def main(argv):
    if len(argv) >= 3 and argv[1] == "--preview":
        config = load_config(argv[3] if len(argv) > 3 else CONFIG_PATH)
        app = App(config, preview=True)
        for path in app.render_previews(argv[2]):
            print(path)
        return 0
    lock = single_instance()
    if lock is None:
        print("ai-usage-xfce is already running", file=sys.stderr)
        return 1
    app = App(load_config())
    app.run()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
