"""CLI for managing Clayde issue state."""

import argparse
import sys

from clayde.state import load_state, save_state


def cmd_status(args: argparse.Namespace) -> None:
    state = load_state()
    issues = state.get("issues", {})
    if not issues:
        print("No issues tracked.")
        return
    for url, entry in issues.items():
        status = entry.get("status", "(unknown)")
        print(f"{status:35} {url}")


def cmd_clear(args: argparse.Namespace) -> None:
    state = load_state()
    issues = state.get("issues", {})
    if args.issue_url not in issues:
        print(f"No state found for: {args.issue_url}", file=sys.stderr)
        sys.exit(1)
    del issues[args.issue_url]
    save_state(state)
    print(f"Cleared state for: {args.issue_url}")


def main() -> None:
    parser = argparse.ArgumentParser(prog="clayde-ctl", description="Manage Clayde issue state.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("status", help="List all tracked issues and their state.")

    clear_parser = subparsers.add_parser("clear", help="Remove state for an issue so it will be retried.")
    clear_parser.add_argument("issue_url", metavar="ISSUE_URL", help="Full GitHub issue URL.")

    args = parser.parse_args()
    if args.command == "status":
        cmd_status(args)
    elif args.command == "clear":
        cmd_clear(args)
