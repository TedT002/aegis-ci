"""Bağımlılıksız ANSI terminal çıktısı."""
import os
import sys

_COLOR = sys.stdout.isatty() and os.getenv("NO_COLOR") is None and os.getenv("TERM") != "dumb"


def _c(code: str, text: str) -> str:
    return f"\033[{code}m{text}\033[0m" if _COLOR else text


def bold(t: str) -> str: return _c("1", t)
def dim(t: str) -> str: return _c("2", t)
def red(t: str) -> str: return _c("31", t)
def green(t: str) -> str: return _c("32", t)
def yellow(t: str) -> str: return _c("33", t)
def cyan(t: str) -> str: return _c("36", t)
def magenta(t: str) -> str: return _c("35", t)


class Console:
    def __init__(self, quiet: bool = False):
        self.quiet = quiet

    def print(self, *args) -> None:
        if not self.quiet:
            print(*args, flush=True)

    def banner(self, title: str) -> None:
        line = "═" * 72
        self.print(cyan(line))
        self.print(cyan("  " + bold(title)))
        self.print(cyan(line))

    def stage(self, idx: int, total: int, name: str) -> None:
        self.print()
        self.print(bold(magenta(f"[{idx}/{total}] {name}")))

    def info(self, msg: str) -> None:
        self.print(f"   {dim('•')} {msg}")

    def ok(self, msg: str) -> None:
        self.print(f"   {green('✔')} {msg}")

    def fail(self, msg: str) -> None:
        self.print(f"   {red('✘')} {msg}")

    def warn(self, msg: str) -> None:
        self.print(f"   {yellow('!')} {msg}")

    def block(self, text: str, max_lines: int = 40) -> None:
        lines = text.rstrip("\n").splitlines()
        for l in lines[:max_lines]:
            if l.startswith("+") and not l.startswith("+++"):
                l = green(l)
            elif l.startswith("-") and not l.startswith("---"):
                l = red(l)
            elif l.startswith("@@"):
                l = cyan(l)
            self.print("     " + dim("│ ") + l)
        if len(lines) > max_lines:
            self.print("     " + dim(f"│ … ({len(lines) - max_lines} more lines)"))
