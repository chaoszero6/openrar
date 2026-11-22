"""Entry point: `python -m openrar` opens the GUI; with a command it runs the CLI."""
import sys


def main():
    from openrar.cli import COMMANDS
    argv = sys.argv[1:]
    first = argv[0].lower() if argv else ""
    if first in COMMANDS and len(argv) > 1 or (first.startswith("rr") and first != "rn") or first in ("-h", "--help", "/?", "help"):
        from openrar.cli import main as cli_main
        return cli_main(argv)
    from openrar.gui.app import main as gui_main
    return gui_main(argv)


if __name__ == "__main__":
    sys.exit(main())
