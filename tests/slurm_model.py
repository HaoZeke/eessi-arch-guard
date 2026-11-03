"""Python reading of Slurm master _proc_stdout.

Values are literal. Lines split on LF only. Anything that is not export,
unset, or print is ignored. A newline inside a value is a second line, and
that line is parsed as its own directive. That is the injection the guard
must not produce.
"""

from __future__ import annotations

from typing import Dict, List, Optional, Tuple


def apply_task_prolog(text: str) -> Tuple[Dict[str, Optional[str]], List[str]]:
    changes: Dict[str, Optional[str]] = {}
    printed: List[str] = []
    # Keep a trailing incomplete line, matching strchr stopping at the buffer end.
    lines = text.split("\n")
    if text.endswith("\n"):
        lines = lines[:-1]
    for line in lines:
        if line.startswith("print "):
            body = line[6:]
            while body[:1].isspace():
                body = body[1:]
            printed.append(body)
        elif line.startswith("export "):
            body = line[7:]
            while body[:1].isspace():
                body = body[1:]
            if "=" not in body:
                continue
            name, value = body.split("=", 1)
            name = name.rstrip(" \t")
            if not name:
                continue
            changes[name] = value
        elif line.startswith("unset "):
            name = line[6:]
            while name[:1].isspace():
                name = name[1:]
            name = name.rstrip(" \t")
            if name:
                changes[name] = None
    return changes, printed
