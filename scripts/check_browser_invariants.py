#!/usr/bin/env python3
"""Fail CI when production code can open an external or unsupervised browser.

The guard scans every file under ``PRODUCTION_ROOTS``.  Python is parsed with
``ast`` (import aliases are resolved per file so renamed imports cannot slip
through); web and shell sources use bounded syntax-aware patterns.  A plain
substring assertion stays as defence in depth but is never the only guard.

Rule identifiers
----------------
Each finding carries a stable rule id so exceptions can be expressed without
weakening a rule for the whole repository:

``webbrowser``                Python ``webbrowser`` module reached by any name.
``subprocess-opener``         ``subprocess``/``asyncio``/``os`` process launch
                              whose command is an OS "open this URL" helper.
``os-exec-opener``            ``os.exec*``/``os.system`` opener.
``window.open``               JS ``window.open`` incl. computed/aliased forms.
``sdk-popup-auth``            Auth-SDK entry point that opens its own window
                              (Firebase/MSAL popup sign-in, GIS ``ux_mode``
                              ``popup``), incl. renamed imports and aliases.
``shell.openExternal``        Electron external open.
``target=_blank``             HTML/JSX/DOM ``target``/``formtarget`` ``_blank``.
``shell-opener``              ``open``/``xdg-open``/``start``/``Start-Process``.
``playwright-persistent``     ``launch_persistent_context``.
``playwright-cdp``            ``connect_over_cdp``.
``playwright-headless``       ``launch`` without a literal ``headless=True``.
``playwright-launch-alias``   ``launch`` captured as a value to dodge the above.
``system-profile-path``       ``executable_path`` / ``user_data_dir``.
``new_context``               ``new_context`` outside ``browser_runtime.py``.
``forbidden-substring``       Defence-in-depth substring backstop.
``parse-error``               Production Python that will not parse.

Allowlist
---------
``ALLOWLIST`` maps ``(path, rule)`` to an ``"owner — rationale"`` string and
suppresses exactly that one rule at exactly that one file.  ``path`` is the
repository-relative POSIX path (an absolute path is also accepted).  It is a
deliberate, reviewable exception list -- not a way to disable a rule globally --
and it **must stay empty** unless a documented, owned exception exists::

    ALLOWLIST = {
        ("app/static/widget.js", "window.open"):
            "platform-team — vendored SDK bootstrap, reviewed 2026-01-01",
    }

A false positive is fixed by narrowing the rule, not by adding an entry here.
"""

from __future__ import annotations

import ast
import re
import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PRODUCTION_ROOTS = ("agents", "app", "services", "scripts")

WEB_SUFFIXES = {
    ".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs",
    ".html", ".htm", ".vue", ".svelte",
}
SHELL_SUFFIXES = {".sh", ".bash", ".zsh", ".ps1"}
TEXT_SUFFIXES = WEB_SUFFIXES | SHELL_SUFFIXES

# Build artefacts and dependency trees: never first-party source.  Note that
# "vendor" and "fixtures" are deliberately NOT name-matched -- a directory that
# merely happens to be called ``vendor`` inside a production root is scanned.
SKIP_PARTS = {"node_modules", "__pycache__", ".opensrc", ".git", ".venv"}
# Specific, known-safe paths (repo-relative prefixes).  Each needs a reason.
SKIP_PATHS = (
    "tests/fixtures",       # synthetic inputs for the test-suite, not shipped
    "app/static/vendor",    # upstream PDF.js/Phosphor bundles, shipped verbatim
)
SELF = Path(__file__).resolve()

# (path, rule) -> "owner — rationale".  See the module docstring.  Keep empty.
ALLOWLIST: dict[tuple[str, str], str] = {}

OPENERS = ("xdg-open", "Start-Process", "open", "start")
OPENER_WORDS = {"open", "xdg-open", "start", "Start-Process"}
# Leading whitespace, command substitution, backticks, pipelines and wrapper
# commands (nohup/exec/sudo/...) all count -- indentation is not a bypass.
SHELL_OPENER = re.compile(
    r"(?:^|[;&|(]|\$\(|`)[ \t]*(?:(?:nohup|exec|sudo|command|env)[ \t]+)*"
    r"(?:" + "|".join(re.escape(word) for word in OPENERS) + r")(?=[ \t]|$)",
    re.M | re.I,
)

SDK_POPUP_RULE = "sdk-popup-auth"
# High-level auth SDKs open the window themselves, so ``window.open`` never
# appears in the source and every pattern above stays quiet.  The names are
# distinctive enough that matching them outright costs no false positives.
FIREBASE_POPUP_CALLS = ("signInWithPopup", "linkWithPopup", "reauthenticateWithPopup")
MSAL_POPUP_CALLS = ("loginPopup", "acquireTokenPopup")
SDK_POPUP_CALLS = FIREBASE_POPUP_CALLS + MSAL_POPUP_CALLS
SDK_POPUP_RULES = (
    # Firebase Auth: signInWithPopup(auth, provider) and its link/reauth twins.
    # Named anywhere -- importing it is already the finding.
    (re.compile(r"\b(" + "|".join(FIREBASE_POPUP_CALLS) + r")\b"),
     "Firebase {} opens a sign-in window"),
    # MSAL: msal.loginPopup({...}) / client.acquireTokenPopup(request).
    (re.compile(r"\b(" + "|".join(MSAL_POPUP_CALLS) + r")\s*\("),
     "MSAL {} opens a sign-in window"),
)
# ``import { signInWithPopup as gp }`` and ``const { loginPopup: gp } = msal``.
SDK_POPUP_RENAME = re.compile(
    r"\b(" + "|".join(SDK_POPUP_CALLS) + r")\b\s*(?:as|:)\s*([A-Za-z_$][\w$]*)"
)
# ``const gp = signInWithPopup;`` -- the value, not a call of it.
SDK_POPUP_VALUE_ALIAS = re.compile(
    r"\b([A-Za-z_$][\w$]*)\s*=\s*(" + "|".join(SDK_POPUP_CALLS) + r")\b"
    r"\s*(?=[;,)\n]|$)"
)
# Google Identity Services: the token/code client opens a popup when asked to.
# Best effort -- the option is matched inside a bounded window after the call.
GIS_POPUP_CLIENT = re.compile(r"\binit(?:Token|Code)Client\s*\(")
GIS_POPUP_UX_MODE = re.compile(r"""\bux_mode\s*:\s*["'`]popup["'`]""")
GIS_OPTION_WINDOW = 400

WINDOW_GLOBALS = r"window|globalThis|self"
# ``window["op" + "en"]`` only reads as "open" once quotes, whitespace and
# concatenation are stripped, so computed access is normalised before matching.
COMPUTED_MEMBER = re.compile(r"\b(?:" + WINDOW_GLOBALS + r")\s*\[([^\]\n]*)\]")
CONCAT_NOISE = re.compile(r"""["'`\s+]""")

WEB_RULES = (
    # window.open / globalThis.open / self.open, including whitespace tricks.
    (re.compile(r"\b(?:" + WINDOW_GLOBALS + r")\s*\.\s*open\s*\("), "window.open",
     "window.open"),
    (re.compile(r"\bshell\s*\.\s*openExternal\s*\("), "shell.openExternal",
     "shell.openExternal"),
    # target="_blank" / 'target=_blank' / target={"_blank"} / target: "_blank"
    (re.compile(
        r"\b(?:form)?target\s*[:=]\s*"
        r"""(?:"_blank"|'_blank'|`_blank`|_blank\b|\{\s*["'`]_blank["'`]\s*\})""",
        re.I,
    ), "target=_blank", 'target="_blank"'),
    # setAttribute("target", "_blank") and setAttribute('formtarget', '_blank')
    (re.compile(
        r"""setAttribute\s*\(\s*["'](?:form)?target["']\s*,\s*["']_blank["']""",
        re.I,
    ), "target=_blank", 'setAttribute target="_blank"'),
)
SHELL_RULES = ((SHELL_OPENER, "shell-opener", "OS browser opener command"),)
COMMON_RULES = (
    (re.compile(r"\blaunch_persistent_context\s*\("), "playwright-persistent",
     "persistent context"),
    (re.compile(r"\bconnect_over_cdp\s*\("), "playwright-cdp", "CDP attach"),
    (re.compile(r"\b(?:user_data_dir|executable_path)\b"), "system-profile-path",
     "system profile/path"),
    (re.compile(r"\bheadless\s*=\s*False\b"), "playwright-headless",
     "headless=False"),
)

FORBIDDEN_SUBSTRINGS = (
    "webbrowser.open",
    "window.open",
    "signInWithPopup",
    "shell.openExternal",
    'target="_blank"',
    "headless=False",
    "launch_persistent_context",
    "connect_over_cdp",
)

SUBPROCESS_CALLS = {
    "run", "Popen", "call", "check_call", "check_output",
    "getoutput", "getstatusoutput",
    "create_subprocess_exec", "create_subprocess_shell",
}
OS_EXEC_CALLS = {
    "execv", "execve", "execvp", "execvpe",
    "execl", "execle", "execlp", "execlpe",
    "spawnv", "spawnvp", "spawnl", "spawnlp", "posix_spawn", "posix_spawnp",
}
# One launch site is allowed in the whole repository; rule 9 pins where.
LAUNCH_SITE = re.compile(
    r"chromium\s*\.\s*launch\s*\(|\.launch\s*\(\s*headless\s*=\s*True"
)
RUNTIME_OWNER = "services/browser_runtime.py"


@dataclass(frozen=True)
class Finding:
    """One rule hit, keyed so the allowlist can address it precisely."""

    path: Path
    line: int
    rule: str
    message: str

    def render(self) -> str:
        return f"{self.path}:{self.line}: {self.message}"


def _rel(path: Path) -> str:
    try:
        return path.resolve().relative_to(ROOT).as_posix()
    except ValueError:
        return path.resolve().as_posix()


def _allowlisted(path: Path, rule: str) -> bool:
    keys = {_rel(path), path.resolve().as_posix(), path.as_posix()}
    return any((key, rule) in ALLOWLIST for key in keys)


def _is_skipped(path: Path) -> bool:
    if path.resolve() == SELF or any(part in SKIP_PARTS for part in path.parts):
        return True
    relative = _rel(path)
    return any(
        relative == skip or relative.startswith(skip + "/") for skip in SKIP_PATHS
    )


def _line(source: str, offset: int) -> int:
    return source.count("\n", 0, offset) + 1


def _const_str(node: ast.AST | None) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def _opener_command(command: str) -> bool:
    """True when a shell command string invokes an OS "open" helper."""
    return bool(SHELL_OPENER.search(command))


class _Bindings:
    """Per-file name resolution: import aliases and simple literal bindings."""

    def __init__(self, tree: ast.AST) -> None:
        self.modules: dict[str, str] = {}      # local name -> dotted module
        self.from_names: dict[str, tuple[str, str]] = {}  # local -> (mod, orig)
        self.lists: dict[str, list[ast.AST]] = {}   # name -> list/tuple literals
        self.dicts: dict[str, list[ast.Dict]] = {}  # name -> dict literals
        self.launch_aliases: set[str] = set()
        self.module_aliases: dict[str, str] = {}    # name -> aliased module
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.asname:
                        self.modules[alias.asname] = alias.name
                    else:
                        top = alias.name.split(".")[0]
                        self.modules[top] = top
            elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
                for alias in node.names:
                    self.from_names[alias.asname or alias.name] = (
                        node.module, alias.name
                    )
            elif isinstance(node, ast.Assign) and len(node.targets) == 1:
                target = node.targets[0]
                if not isinstance(target, ast.Name):
                    continue
                value = node.value
                if isinstance(value, (ast.List, ast.Tuple)):
                    self.lists.setdefault(target.id, []).append(value)
                elif isinstance(value, ast.Dict):
                    self.dicts.setdefault(target.id, []).append(value)
                elif isinstance(value, ast.Attribute) and value.attr == "launch":
                    self.launch_aliases.add(target.id)
                elif isinstance(value, ast.Name):
                    self.module_aliases[target.id] = value.id

    def module_of(self, name: str) -> str:
        """Resolve a local name to the module it ultimately refers to."""
        seen: set[str] = set()
        current = name
        while current not in seen:
            seen.add(current)
            if current in self.modules:
                return self.modules[current]
            if current in self.module_aliases:
                current = self.module_aliases[current]
                continue
            break
        return ""

    def is_module(self, node: ast.AST, module: str) -> bool:
        return isinstance(node, ast.Name) and self.module_of(node.id) == module

    def list_literals(self, node: ast.AST) -> list[ast.AST]:
        if isinstance(node, (ast.List, ast.Tuple)):
            return [node]
        if isinstance(node, ast.Name):
            return self.lists.get(node.id, [])
        return []

    def dict_literals(self, node: ast.AST) -> list[ast.Dict]:
        if isinstance(node, ast.Dict):
            return [node]
        if isinstance(node, ast.Name):
            return self.dicts.get(node.id, [])
        return []


def _first_opener(node: ast.AST, binds: _Bindings) -> bool:
    """True when ``node`` is (or names) a list literal starting with an opener."""
    for literal in binds.list_literals(node):
        elements = getattr(literal, "elts", [])
        if elements and _const_str(elements[0]) in OPENER_WORDS:
            return True
    return False


def _is_subprocess_call(func: ast.AST, attr: str, binds: _Bindings) -> bool:
    if attr in SUBPROCESS_CALLS:
        return True
    if isinstance(func, ast.Name):
        origin = binds.from_names.get(func.id)
        return bool(origin and origin[0] in {"subprocess", "asyncio"}
                    and origin[1] in SUBPROCESS_CALLS)
    return False


def _webbrowser_findings(
    path: Path, node: ast.Call, func: ast.AST, attr: str, binds: _Bindings
) -> list[Finding]:
    """Rule 1: reach the ``webbrowser`` module by any name or indirection."""
    findings: list[Finding] = []
    # webbrowser.open(...) / wb.open(...) after ``import webbrowser as wb``
    if attr.startswith("open") and isinstance(func, ast.Attribute):
        if binds.is_module(func.value, "webbrowser"):
            findings.append(Finding(path, node.lineno, "webbrowser",
                                    f"webbrowser.{attr}"))
    # ``from webbrowser import open as show`` then ``show(url)``
    if isinstance(func, ast.Name):
        origin = binds.from_names.get(func.id)
        if origin and origin[0] == "webbrowser" and origin[1].startswith("open"):
            findings.append(Finding(
                path, node.lineno, "webbrowser",
                f"webbrowser.{origin[1]} imported as {func.id}",
            ))
    # getattr(webbrowser, "open") / getattr(mod, "webbrowser") indirection
    name = func.id if isinstance(func, ast.Name) else attr
    if name in {"getattr", "__import__", "import_module"}:
        literals = {_const_str(arg) for arg in node.args}
        if "webbrowser" in literals:
            findings.append(Finding(path, node.lineno, "webbrowser",
                                    f"webbrowser reached via {name}()"))
        elif name == "getattr" and node.args and binds.is_module(
            node.args[0], "webbrowser"
        ):
            findings.append(Finding(path, node.lineno, "webbrowser",
                                    "webbrowser.open via getattr()"))
    return findings


def _process_findings(
    path: Path, node: ast.Call, func: ast.AST, attr: str, binds: _Bindings
) -> list[Finding]:
    """Rule 2: any process launch whose command is an OS browser opener."""
    findings: list[Finding] = []
    shell_true = any(
        kw.arg == "shell" and isinstance(kw.value, ast.Constant)
        and kw.value.value is True
        for kw in node.keywords
    )
    if _is_subprocess_call(func, attr, binds):
        first = node.args[0] if node.args else None
        command = _const_str(first)
        if command is not None and _opener_command(command):
            label = "subprocess OS browser opener"
            if shell_true:
                label = "subprocess shell=True OS browser opener"
            findings.append(Finding(path, node.lineno, "subprocess-opener", label))
        elif any(_first_opener(arg, binds) for arg in node.args):
            findings.append(Finding(path, node.lineno, "subprocess-opener",
                                    "subprocess OS browser opener (argv list)"))
        elif attr == "create_subprocess_exec" and node.args:
            if _const_str(node.args[0]) in OPENER_WORDS:
                findings.append(Finding(path, node.lineno, "subprocess-opener",
                                        "subprocess OS browser opener"))
    # os.system("open ..."), os.execvp("open", ...), os.spawnlp(...) -- reached
    # as an attribute on the module or imported directly off it.
    os_call = ""
    if isinstance(func, ast.Attribute) and binds.is_module(func.value, "os"):
        os_call = attr
    elif isinstance(func, ast.Name):
        origin = binds.from_names.get(func.id)
        os_call = origin[1] if origin and origin[0] == "os" else ""
    if os_call == "system":
        command = _const_str(node.args[0]) if node.args else None
        if command is not None and _opener_command(command):
            findings.append(Finding(path, node.lineno, "os-exec-opener",
                                    "os.system OS browser opener"))
    elif os_call in OS_EXEC_CALLS:
        literals = [_const_str(arg) for arg in node.args[:2]]
        if any(value in OPENER_WORDS for value in literals) or any(
            _first_opener(arg, binds) for arg in node.args
        ):
            findings.append(Finding(path, node.lineno, "os-exec-opener",
                                    f"os.{os_call} OS browser opener"))
    return findings


def _launch_findings(
    path: Path, node: ast.Call, func: ast.AST, attr: str, binds: _Bindings
) -> list[Finding]:
    """Rules 6 and 7: Playwright launch shape, aliases and **kwargs smuggling."""
    findings: list[Finding] = []
    aliased = isinstance(func, ast.Name) and func.id in binds.launch_aliases
    if attr == "launch" or aliased:
        headless = next(
            (kw.value for kw in node.keywords if kw.arg == "headless"), None
        )
        if headless is None:
            for keyword in node.keywords:
                if keyword.arg is not None:
                    continue
                for mapping in binds.dict_literals(keyword.value):
                    for key, value in zip(mapping.keys, mapping.values):
                        if _const_str(key) == "headless":
                            headless = value
        if not isinstance(headless, ast.Constant) or headless.value is not True:
            findings.append(Finding(path, node.lineno, "playwright-headless",
                                    "launch headless must be literal True"))
    if attr in {"launch_persistent_context", "connect_over_cdp"}:
        rule = ("playwright-persistent" if attr == "launch_persistent_context"
                else "playwright-cdp")
        findings.append(Finding(path, node.lineno, rule,
                                f"forbidden Playwright {attr}"))
    for keyword in node.keywords:
        if keyword.arg in {"executable_path", "user_data_dir"}:
            findings.append(Finding(path, node.lineno, "system-profile-path",
                                    f"forbidden {keyword.arg}"))
        elif keyword.arg is None:  # **{...} / **kwargs dict unpacking
            for mapping in binds.dict_literals(keyword.value):
                for key in mapping.keys:
                    name = _const_str(key)
                    if name in {"executable_path", "user_data_dir"}:
                        findings.append(Finding(
                            path, node.lineno, "system-profile-path",
                            f"forbidden {name} via ** unpacking",
                        ))
    # getattr(chromium, "launch") / getattr(browser, "new_context")
    if isinstance(func, ast.Name) and func.id == "getattr":
        literals = {_const_str(arg) for arg in node.args}
        if "launch" in literals:
            findings.append(Finding(path, node.lineno, "playwright-launch-alias",
                                    "playwright launch reached via getattr()"))
    return findings


def _python_findings(path: Path, source: str) -> list[Finding]:
    findings: list[Finding] = []
    try:
        tree = ast.parse(source, filename=str(path))
    except SyntaxError as exc:
        return [Finding(path, exc.lineno or 1, "parse-error",
                        "cannot parse production Python")]
    binds = _Bindings(tree)
    runtime_owner = path.resolve() == (ROOT / RUNTIME_OWNER).resolve()
    for name in sorted(binds.launch_aliases):
        findings.append(Finding(path, 1, "playwright-launch-alias",
                                f"playwright launch captured as alias {name!r}"))
    for node in ast.walk(tree):
        # Rule 7 backstop: ``helper = browser.new_context`` is a bare attribute
        # load, so match the attribute itself rather than only the call.
        if isinstance(node, ast.Attribute) and node.attr == "new_context":
            if not runtime_owner:
                findings.append(Finding(path, node.lineno, "new_context",
                                        "browser.new_context outside supervisor"))
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        attr = func.attr if isinstance(func, ast.Attribute) else ""
        if not runtime_owner and isinstance(func, ast.Name) and func.id == "getattr":
            if "new_context" in {_const_str(arg) for arg in node.args}:
                findings.append(Finding(
                    path, node.lineno, "new_context",
                    "browser.new_context outside supervisor (via getattr)",
                ))
        findings.extend(_webbrowser_findings(path, node, func, attr, binds))
        findings.extend(_process_findings(path, node, func, attr, binds))
        findings.extend(_launch_findings(path, node, func, attr, binds))
    return findings


def _reads_as_open(expression: str) -> bool:
    """``"open"``, ``'op' + 'en'`` and ``` `open` ``` all read as ``open``."""
    return "open" in CONCAT_NOISE.sub("", expression).lower()


def _window_open_findings(path: Path, source: str) -> list[Finding]:
    """Rule 3: computed ``window[...]`` access and ``const w = window`` aliases."""
    findings: list[Finding] = []
    for match in COMPUTED_MEMBER.finditer(source):
        if _reads_as_open(match.group(1)):
            findings.append(Finding(path, _line(source, match.start()),
                                    "window.open",
                                    "window.open via computed member access"))
    for match in re.finditer(
        r"(?:(?:const|let|var)\s+)?([A-Za-z_$][\w$]*)\s*=\s*window\b\s*(?=[;,)\n]|$)",
        source,
    ):
        alias = match.group(1)
        direct = re.compile(r"\b" + re.escape(alias) + r"\s*\.\s*open\s*\(")
        computed = re.compile(r"\b" + re.escape(alias) + r"\s*\[([^\]\n]*)\]")
        hits = list(direct.finditer(source)) + [
            hit for hit in computed.finditer(source) if _reads_as_open(hit.group(1))
        ]
        for hit in hits:
            findings.append(Finding(path, _line(source, hit.start()),
                                    "window.open",
                                    f"window.open via alias {alias!r}"))
    return findings


def _sdk_popup_findings(path: Path, source: str) -> list[Finding]:
    """Rule ``sdk-popup-auth``: SDK calls that open a window on your behalf.

    Human OAuth must replace the current tab (docs/22), so the popup entry
    points of the auth SDKs are forbidden outright.  Names are resolved per
    file the way the ``webbrowser`` rule resolves Python imports: the direct
    name, a renamed import or destructuring (``as`` / ``:``), and a plain value
    alias all reach the same function, so all three are reported.
    """
    findings: list[Finding] = []
    for pattern, label in SDK_POPUP_RULES:
        for match in pattern.finditer(source):
            findings.append(Finding(path, _line(source, match.start()),
                                    SDK_POPUP_RULE, label.format(match.group(1))))
    aliases = {match.group(2): match.group(1)
               for match in SDK_POPUP_RENAME.finditer(source)}
    aliases.update({match.group(1): match.group(2)
                    for match in SDK_POPUP_VALUE_ALIAS.finditer(source)})
    for alias, original in aliases.items():
        if alias in SDK_POPUP_CALLS:  # already reported under its own name
            continue
        for hit in re.finditer(r"\b" + re.escape(alias) + r"\s*\(", source):
            findings.append(Finding(
                path, _line(source, hit.start()), SDK_POPUP_RULE,
                f"{original} opens a sign-in window, via alias {alias!r}",
            ))
    for match in GIS_POPUP_CLIENT.finditer(source):
        options = source[match.end():match.end() + GIS_OPTION_WINDOW]
        if GIS_POPUP_UX_MODE.search(options):
            findings.append(Finding(
                path, _line(source, match.start()), SDK_POPUP_RULE,
                'Google Identity Services client with ux_mode: "popup"',
            ))
    return findings


def _text_findings(path: Path, source: str) -> list[Finding]:
    suffix = path.suffix.lower()
    rules = list(COMMON_RULES)
    if suffix in WEB_SUFFIXES:
        rules += list(WEB_RULES)
    if suffix in SHELL_SUFFIXES:
        rules += list(SHELL_RULES)
    findings: list[Finding] = []
    for pattern, rule, label in rules:
        for match in pattern.finditer(source):
            findings.append(Finding(path, _line(source, match.start()), rule, label))
    if suffix in WEB_SUFFIXES:
        findings.extend(_window_open_findings(path, source))
        findings.extend(_sdk_popup_findings(path, source))
    return findings


def _substring_findings(path: Path, source: str) -> list[Finding]:
    findings: list[Finding] = []
    for token in FORBIDDEN_SUBSTRINGS:
        offset = source.find(token)
        if offset >= 0:
            findings.append(Finding(path, _line(source, offset),
                                    "forbidden-substring",
                                    f"forbidden substring {token}"))
    return findings


def collect_findings(paths: list[Path]) -> list[Finding]:
    findings: list[Finding] = []
    for path in paths:
        if not path.is_file() or _is_skipped(path):
            continue
        suffix = path.suffix.lower()
        if suffix == ".py":
            source = path.read_text(encoding="utf-8", errors="replace")
            findings.extend(_python_findings(path, source))
            # Backstop for shapes ast cannot see, e.g. ``opts["user_data_dir"] =``
            # built up across statements and then splatted into launch().
            findings.extend(_text_findings(path, source))
        elif suffix in TEXT_SUFFIXES:
            source = path.read_text(encoding="utf-8", errors="replace")
            findings.extend(_text_findings(path, source))
        else:
            continue
        findings.extend(_substring_findings(path, source))
    return [item for item in findings if not _allowlisted(item.path, item.rule)]


def check_paths(paths: list[Path]) -> list[str]:
    rendered: list[str] = []
    for finding in collect_findings(paths):
        text = finding.render()
        if text not in rendered:
            rendered.append(text)
    return rendered


def production_paths() -> list[Path]:
    return [
        path
        for root in PRODUCTION_ROOTS
        for path in (ROOT / root).rglob("*")
        if path.is_file()
    ]


def launch_sites(paths: list[Path]) -> list[str]:
    """Rule 9: every literal Chromium launch site across production roots."""
    sites: list[str] = []
    for path in paths:
        if not path.is_file() or _is_skipped(path):
            continue
        if path.suffix.lower() not in TEXT_SUFFIXES | {".py"}:
            continue
        source = path.read_text(encoding="utf-8", errors="replace")
        for match in LAUNCH_SITE.finditer(source):
            sites.append(f"{_rel(path)}:{_line(source, match.start())}")
    return sites


def main() -> int:
    paths = production_paths()
    findings = check_paths(paths)
    if findings:
        print("Browser invariant violations:", file=sys.stderr)
        print("\n".join(f"- {item}" for item in findings), file=sys.stderr)
        return 1
    sites = launch_sites(paths)
    if len(sites) != 1:
        print(
            f"Expected one literal headless launch site; found {len(sites)}: "
            f"{', '.join(sites) or 'none'}",
            file=sys.stderr,
        )
        return 1
    if not sites[0].startswith(RUNTIME_OWNER + ":"):
        print(
            f"Launch site must live in {RUNTIME_OWNER}; found {sites[0]}",
            file=sys.stderr,
        )
        return 1
    print("Browser invariants: pass")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
