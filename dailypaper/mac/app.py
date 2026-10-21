"""macOS menu bar app.

Shows today's paper and blog posts in the menu; clicking one opens it in the browser and
hovering shows its summary. Checks whether today's reading exists at launch, after the Mac
wakes, and every 15 minutes (so a machine left on overnight still gets a fresh paper). Claude calls run on a worker
thread; anything touching the UI is pushed back to the main thread with callAfter.
"""

import logging
import threading
import webbrowser
from datetime import date

import anthropic
import objc
import rumps
from AppKit import NSApplication, NSWorkspace
from Foundation import NSObject
from PyObjCTools import AppHelper

from ..core.config import Config, get_api_key, set_api_key
from ..core.paths import data_dir
from ..core.service import DailyPaper
from ..core.storage import BlogPost, Paper, Storage
from . import login_item

log = logging.getLogger(__name__)

ICON = "📄"
CHECK_INTERVAL = 15 * 60
HISTORY_ITEMS = 20


def stars_label(stars: int | None) -> str:
    return "★" * (stars or 0) + "☆" * (5 - (stars or 0))


def truncate(text: str, n: int = 60) -> str:
    return text if len(text) <= n else text[: n - 1] + "…"


def paper_tooltip(paper: Paper) -> str:
    meta = " · ".join(
        part for part in (
            ", ".join(paper.authors[:3]) + (" et al." if len(paper.authors) > 3 else ""),
            f"{paper.source} {paper.year or ''}".strip(),
            f"~{paper.reading_minutes} min read" if paper.reading_minutes else "",
        ) if part
    )
    return f"{paper.title}\n{meta}\n\n{paper.summary}\n\nWhy this one: {paper.why}"


def _link_item(title: str, tooltip: str, callback) -> rumps.MenuItem:
    """A menu item that opens a link when clicked and shows a summary on hover."""
    item = rumps.MenuItem(title, callback=callback)
    item._menuitem.setToolTip_(tooltip)
    return item


class WakeObserver(NSObject):
    def initWithCallback_(self, callback):
        self = objc.super(WakeObserver, self).init()
        if self is not None:
            self.callback = callback
        return self

    def didWake_(self, _notification):
        self.callback()


class DailyPaperApp(rumps.App):
    def __init__(self):
        super().__init__("DailyPaper", title=ICON, quit_button=None)
        self.config = Config.load()
        self.storage = Storage()  # main-thread connection; workers open their own
        self.busy = False
        self.error: str | None = None
        self.last_checked: date | None = None

        self.timer = rumps.Timer(lambda _: self.check_due(), CHECK_INTERVAL)
        self.timer.start()
        self.wake_observer = WakeObserver.alloc().initWithCallback_(self.check_due)
        NSWorkspace.sharedWorkspace().notificationCenter().addObserver_selector_name_object_(
            self.wake_observer, "didWake:", "NSWorkspaceDidWakeNotification", None
        )
        self.rebuild_menu()
        AppHelper.callLater(1.0, self.first_run)

    # --- scheduling ---------------------------------------------------------

    def first_run(self):
        if not get_api_key():
            self.prompt_api_key()
        if not self.config.interests:
            self.edit_interests()
        self.check_due()

    def check_due(self):
        if self.busy:
            return
        today = date.today()
        if self.storage.latest_for(today):
            if self.last_checked != today:  # date rolled over to a day that already has a paper
                self.last_checked = today
                self.rebuild_menu()
            return
        self.last_checked = today
        self.fetch()

    def fetch(self):
        self.busy, self.error = True, None
        self.rebuild_menu()
        config = self.config

        def work():
            error = None
            try:
                DailyPaper(storage=Storage(), config=config).fetch()
            except anthropic.AuthenticationError:
                error = "API key missing or invalid (Preferences → Set API key)"
            except anthropic.APIConnectionError:
                error = "offline, will retry"
            except Exception as exc:  # shown in the menu; retried on the next check
                log.exception("Fetch failed")
                error = str(exc)
            AppHelper.callAfter(self.fetch_done, error)

        threading.Thread(target=work, daemon=True).start()

    def fetch_done(self, error: str | None):
        self.busy, self.error = False, error
        self.rebuild_menu()

    # --- menu ---------------------------------------------------------------

    def rebuild_menu(self):
        today = self.storage.latest_for(date.today())
        blogs = self.storage.blogs_for(today.id) if today else []
        self.title = ICON + ("…" if self.busy else "!" if self.error else "•" if today and not today.opened_at else "")

        items: list = [rumps.MenuItem("Today's paper")]
        if today:
            items.append(_link_item(f"{ICON} {truncate(today.title)}", paper_tooltip(today),
                                    lambda _: self.open_paper(today)))
            rate = rumps.MenuItem("    Rate it")
            for n in range(1, 6):
                item = rumps.MenuItem(stars_label(n), callback=lambda _, n=n: self.rate(today, n))
                item.state = int(today.stars == n)
                rate.add(item)
            items.append(rate)
        elif self.busy:
            items.append(rumps.MenuItem("    Finding today's reading…"))
        else:
            items.append(rumps.MenuItem("    Nothing yet today"))
        if self.error:
            items.append(rumps.MenuItem(f"    Couldn't fetch: {truncate(self.error, 70)}"))
            items.append(rumps.MenuItem("    Try again", callback=lambda _: self.fetch()))

        if blogs:
            items.append(rumps.separator)
            items.append(rumps.MenuItem("Blog posts"))
            for blog in blogs:
                label = f"✎ {truncate(blog.title, 50)} · {truncate(blog.site, 20)}"
                items.append(_link_item(label, blog.summary, lambda _, b=blog: self.open_blog(b)))
        items.append(rumps.separator)

        items.append(rumps.MenuItem("Get another paper today", callback=None if self.busy else lambda _: self.fetch()))

        history = rumps.MenuItem("History")
        past = self.storage.history(limit=HISTORY_ITEMS)
        for p in past:
            history.add(_link_item(f"{p.suggested_on}  {stars_label(p.stars)}  {truncate(p.title, 50)}",
                                   paper_tooltip(p), lambda _, p=p: self.open_paper(p)))
        if not past:
            history.add(rumps.MenuItem("Nothing yet"))
        items.append(history)

        prefs = rumps.MenuItem("Preferences")
        prefs.add(rumps.MenuItem("Edit interests…", callback=lambda _: self.edit_interests()))
        prefs.add(rumps.MenuItem("Set API key…", callback=lambda _: self.prompt_api_key()))
        login = rumps.MenuItem("Start at login", callback=lambda _: self.toggle_login_item())
        login.state = int(login_item.is_installed())
        prefs.add(login)
        items.append(prefs)

        items.append(rumps.separator)
        items.append(rumps.MenuItem("Quit DailyPaper", callback=lambda _: rumps.quit_application()))

        self.menu.clear()
        self.menu.update(items)

    # --- actions ------------------------------------------------------------

    def open_paper(self, paper: Paper):
        webbrowser.open(paper.url)
        self.storage.mark_opened(paper.id)
        self.rebuild_menu()

    def open_blog(self, blog: BlogPost):
        webbrowser.open(blog.url)
        self.storage.mark_blog_opened(blog.id)

    def rate(self, paper: Paper, stars: int):
        config = self.config

        def work():
            try:
                DailyPaper(storage=Storage(), config=config).rate(paper.id, stars)
            except Exception:
                log.exception("Rating failed")
            AppHelper.callAfter(self.rebuild_menu)

        threading.Thread(target=work, daemon=True).start()

    def edit_interests(self):
        _activate()
        response = rumps.Window(
            title="Your interests",
            message="What do you like reading about? Topics, fields, favourite papers. "
                    "Your star ratings refine this over time.",
            default_text=self.config.interests,
            ok="Save", cancel="Cancel", dimensions=(360, 120),
        ).run()
        if response.clicked == 1:
            self.config.interests = response.text.strip()
            self.config.save()

    def prompt_api_key(self):
        _activate()
        response = rumps.Window(
            title="Anthropic API key",
            message="DailyPaper uses Claude to pick papers. Paste your API key from "
                    "platform.claude.com. It's stored in your macOS Keychain.",
            default_text="", ok="Save", cancel="Cancel", dimensions=(360, 24),
        ).run()
        if response.clicked == 1 and response.text.strip():
            set_api_key(response.text)

    def toggle_login_item(self):
        try:
            login_item.uninstall() if login_item.is_installed() else login_item.install()
        except Exception as exc:
            rumps.alert("Couldn't change the login item", str(exc))
        self.rebuild_menu()


def _activate():
    NSApplication.sharedApplication().activateIgnoringOtherApps_(True)


def main():
    logging.basicConfig(
        filename=data_dir() / "app.log", level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    DailyPaperApp().run()


if __name__ == "__main__":
    main()
