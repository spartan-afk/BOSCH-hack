"""Terminal presentation: step banners, colours, the result box. Plain text when not a TTY."""

import os
import sys
import textwrap

_COLOR = sys.stdout.isatty() and os.getenv("NO_COLOR") is None
if _COLOR and os.name == "nt":
    os.system("")          # enables ANSI escape handling in the Windows console

CODES = {"bold": "1", "dim": "2", "red": "31", "green": "32", "yellow": "33", "blue": "34",
         "magenta": "35", "cyan": "36"}
WIDTH = 78


def c(text, *styles) -> str:
    if not _COLOR or not styles:
        return str(text)
    return f"\033[{';'.join(CODES[s] for s in styles)}m{text}\033[0m"


def step(n: int, total: int, title: str, who: str = "") -> None:
    tag = c(f" {who} ", "bold", "magenta") if who == "AI AGENT" else c(f" {who} ", "bold", "cyan") if who else ""
    print()
    print(c(f"[{n}/{total}] {title}", "bold") + ("  " + tag if tag else ""))
    print(c("-" * WIDTH, "dim"))


def info(label: str, value) -> None:
    print(f"  {c(label + ':', 'dim')} {value}")


def ok(text: str) -> None:
    print(f"  {c('OK', 'green', 'bold')}  {text}")


def warn(text: str) -> None:
    print(f"  {c('!!', 'yellow', 'bold')}  {text}")


def bad(text: str) -> None:
    print(f"  {c('XX', 'red', 'bold')}  {text}")


def wrap(text: str, indent: int = 6) -> str:
    return textwrap.fill(str(text), WIDTH, initial_indent=" " * indent, subsequent_indent=" " * indent)


def box(title: str, rows: list[tuple[str, str]]) -> None:
    print()
    print(c("=" * WIDTH, "bold"))
    print(c(f"  {title}", "bold"))
    print(c("=" * WIDTH, "bold"))
    width = max(len(k) for k, _ in rows) + 2
    for key, value in rows:
        lines = textwrap.wrap(str(value), WIDTH - width - 4) or [""]
        print(f"  {c(key.ljust(width), 'bold')}{lines[0]}")
        for line in lines[1:]:
            print(" " * (width + 2) + line)
    print(c("=" * WIDTH, "bold"))
