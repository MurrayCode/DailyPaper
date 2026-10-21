import argparse
import getpass
import sys
import textwrap

from .core.config import ENABLED_PAPER_TYPES, Config, set_api_key
from .core.service import DailyPaper
from .core.storage import BlogPost, Paper


def print_paper(paper: Paper) -> None:
    stars = f"  [{paper.stars}/5]" if paper.stars else ""
    print(f"#{paper.id}  {paper.suggested_on}{stars}")
    print(paper.title)
    print(f"{', '.join(paper.authors)} · {paper.source} {paper.year or ''} · {paper.paper_type}"
          + (f" · ~{paper.reading_minutes} min" if paper.reading_minutes else ""))
    print()
    print(textwrap.fill(paper.summary, 88))
    print()
    print(textwrap.fill(f"Why: {paper.why}", 88))
    print(paper.url)


def print_blogs(blogs: list[BlogPost]) -> None:
    if not blogs:
        return
    print("\nBlog posts:")
    for b in blogs:
        print(f"- {b.title} ({b.site})")
        print(textwrap.indent(textwrap.fill(b.summary, 84), "  "))
        print(f"  {b.url}")


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="dailypaper", description="A paper a day, picked by Claude.")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("app", help="run the macOS menu bar app")
    sub.add_parser("today", help="show today's paper and blog posts, fetching them if needed")
    sub.add_parser("another", help="fetch an extra paper (and blog posts) for today")
    rate = sub.add_parser("rate", help="rate a paper 1-5 (today's by default)")
    rate.add_argument("stars", type=int, choices=range(1, 6))
    rate.add_argument("--id", type=int, help="paper id (see `history`)")
    hist = sub.add_parser("history", help="list past suggestions")
    hist.add_argument("-n", type=int, default=20)
    sub.add_parser("profile", help="show the taste profile built from your ratings")
    setup = sub.add_parser("setup", help="set interests, paper types and API key")
    setup.add_argument("--interests")
    setup.add_argument("--types", help=f"comma-separated, currently only: {','.join(ENABLED_PAPER_TYPES)}")
    setup.add_argument("--api-key", action="store_true", help="prompt for an API key to store in the Keychain")
    sub.add_parser("install-login-item", help="start the menu bar app at login (macOS)")
    sub.add_parser("uninstall-login-item", help="stop starting at login (macOS)")
    args = parser.parse_args(argv)

    if args.cmd == "app":
        from .mac.app import main as run_app
        run_app()
        return
    if args.cmd in ("install-login-item", "uninstall-login-item"):
        from .mac import login_item
        if args.cmd == "install-login-item":
            print(f"Installed {login_item.install()}")
        else:
            login_item.uninstall()
            print("Removed login item")
        return
    if args.cmd == "setup":
        config = Config.load()
        if args.interests is not None:
            config.interests = args.interests
        if args.types:
            types = sorted({t.strip() for t in args.types.split(",")} & set(ENABLED_PAPER_TYPES))
            if not types:
                sys.exit(f"--types must include one of: {', '.join(ENABLED_PAPER_TYPES)}")
            config.paper_types = types
        config.save()
        if args.api_key:
            set_api_key(getpass.getpass("Anthropic API key: "))
        print(f"Interests: {config.interests or '(none)'}\nTypes: {', '.join(config.paper_types)}")
        return

    dp = DailyPaper()
    if args.cmd == "today":
        paper, _ = dp.ensure_today()
        print_paper(paper)
        print_blogs(dp.storage.blogs_for(paper.id))
    elif args.cmd == "another":
        paper = dp.fetch()
        print_paper(paper)
        print_blogs(dp.storage.blogs_for(paper.id))
    elif args.cmd == "rate":
        paper = dp.storage.get(args.id) if args.id else dp.today()
        if not paper:
            sys.exit("No such paper")
        dp.rate(paper.id, args.stars)
        print(f"Rated \"{paper.title}\" {args.stars}/5")
    elif args.cmd == "history":
        for p in dp.storage.history(limit=args.n):
            stars = f"{p.stars}/5" if p.stars else "  - "
            print(f"#{p.id:<4} {p.suggested_on}  {stars}  {p.title}")
    elif args.cmd == "profile":
        print(dp.storage.get_meta("profile") or "No profile yet (built after 7 ratings).")


if __name__ == "__main__":
    main()
