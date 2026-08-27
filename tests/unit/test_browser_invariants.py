"""Synthetic forbidden-construct coverage for the static guard.

The parametrised ``MUTATIONS`` block is the important part: every entry is a
bypass that an earlier version of the checker let through, so a regression in
any rule family fails here rather than in production.
"""

from pathlib import Path

import pytest

from scripts import check_browser_invariants as guard
from scripts.check_browser_invariants import check_paths


def test_static_checker_rejects_python_and_web_external_launches(tmp_path: Path):
    py = tmp_path / "bad.py"
    py.write_text(
        "import webbrowser, subprocess\n"
        "webbrowser.open('https://example.com')\n"
        "subprocess.run(['open', 'https://example.com'])\n"
        "browser.new_context()\n"
        "playwright.chromium.launch(headless=False)\n"
    )
    html = tmp_path / "bad.html"
    html.write_text(
        '<a target="_blank">x</a><script>window.open("https://x")</script>'
    )
    findings = "\n".join(check_paths([py, html]))
    for expected in (
        "webbrowser.open", "subprocess OS browser opener", "new_context",
        "headless must be literal True", 'target="_blank"', "window.open",
    ):
        assert expected in findings


def test_static_checker_accepts_literal_headless_supervisor_shape(tmp_path: Path):
    safe = tmp_path / "safe.py"
    safe.write_text("await playwright.chromium.launch(headless=True)\n")
    assert check_paths([safe]) == []


@pytest.mark.parametrize(
    ("suffix", "source", "expected"),
    [
        (".py", "await p.chromium.launch(headless=config.headless)", "literal True"),
        (".py", "await p.chromium.launch_persistent_context('/tmp/profile')", "persistent"),
        (".py", "await p.chromium.connect_over_cdp('ws://x')", "connect_over_cdp"),
        (".py", "await p.chromium.launch(headless=True, executable_path='/bin/x')", "executable_path"),
        (".py", "subprocess.Popen(['xdg-open', 'https://x'])", "subprocess"),
        (".js", "electron.shell.openExternal('https://x')", "openExternal"),
        (".sh", "Start-Process https://x", "opener command"),
    ],
)
def test_each_forbidden_external_launch_family_is_rejected(
    tmp_path: Path, suffix: str, source: str, expected: str
):
    path = tmp_path / f"bad{suffix}"
    path.write_text(source)
    assert expected in "\n".join(check_paths([path]))


# (id, suffix, source, expected fragment) -- one row per known bypass.
MUTATIONS = [
    # 1. webbrowser reached under a different name or through indirection.
    ("webbrowser-import-as", ".py",
     "import webbrowser as wb\nwb.open(url)\n", "webbrowser"),
    ("webbrowser-from-import-as", ".py",
     "from webbrowser import open as show\nshow(url)\n", "webbrowser"),
    ("webbrowser-getattr", ".py",
     "import webbrowser\ngetattr(webbrowser, 'open')(url)\n", "webbrowser"),
    ("webbrowser-import-module", ".py",
     "import importlib\nimportlib.import_module('webbrowser').open(url)\n",
     "webbrowser"),
    ("webbrowser-dunder-import", ".py",
     "__import__('webbrowser').open(url)\n", "webbrowser"),
    # 2. process launches that are not a literal first argument.
    ("os-system-opener", ".py",
     "import os\nos.system('open https://x')\n", "OS browser opener"),
    ("shell-true-opener", ".py",
     "import subprocess\nsubprocess.run('xdg-open https://x', shell=True)\n",
     "OS browser opener"),
    ("shell-true-chained-opener", ".py",
     "import subprocess\nsubprocess.call('cd /tmp && open https://x', shell=True)\n",
     "OS browser opener"),
    ("argv-list-variable", ".py",
     "import subprocess\n"
     "def go():\n"
     "    cmd = ['open', 'https://x']\n"
     "    subprocess.Popen(cmd)\n",
     "OS browser opener"),
    ("os-execvp", ".py",
     "import os\nos.execvp('xdg-open', ['xdg-open', 'https://x'])\n",
     "OS browser opener"),
    ("os-execlp", ".py",
     "import os\nos.execlp('open', 'open', 'https://x')\n", "OS browser opener"),
    ("asyncio-create-subprocess-exec", ".py",
     "import asyncio\n"
     "async def go():\n"
     "    await asyncio.create_subprocess_exec('open', 'https://x')\n",
     "OS browser opener"),
    ("asyncio-create-subprocess-shell", ".py",
     "import asyncio\n"
     "async def go():\n"
     "    await asyncio.create_subprocess_shell('start https://x')\n",
     "OS browser opener"),
    ("subprocess-from-import", ".py",
     "from subprocess import run\nrun('open https://x', shell=True)\n",
     "OS browser opener"),
    ("os-system-from-import", ".py",
     "from os import system\nsystem('xdg-open https://x')\n",
     "OS browser opener"),
    # 3. window.open through computed access or an alias.
    ("window-bracket-literal", ".js",
     'window["open"]("https://x");\n', "window.open"),
    ("window-bracket-concat", ".js",
     'window["op" + "en"]("https://x");\n', "window.open"),
    ("window-alias", ".js",
     'const w = window;\nw.open("https://x");\n', "window.open"),
    ("globalthis-open", ".js",
     'globalThis.open("https://x");\n', "window.open"),
    # 4. target="_blank" spelt every other way.
    ("target-unquoted", ".html", "<a href=x target=_blank>go</a>\n", "_blank"),
    ("target-single-quoted", ".html", "<a href=x target='_blank'>go</a>\n", "_blank"),
    ("target-jsx-expression", ".jsx",
     '<a href={u} target={"_blank"}>go</a>\n', "_blank"),
    ("target-set-attribute", ".js",
     'el.setAttribute("target", "_blank");\n', "_blank"),
    ("target-object-literal", ".js", 'const a = { target: "_blank" };\n', "_blank"),
    ("formtarget", ".html", '<button formtarget="_blank">go</button>\n', "_blank"),
    # 5. shell openers that are not at column zero.
    ("shell-indented", ".sh",
     "if true; then\n    xdg-open https://x\nfi\n", "opener command"),
    ("shell-command-substitution", ".sh", "URL=$(open https://x)\n", "opener command"),
    ("shell-exec", ".sh", "exec open https://x\n", "opener command"),
    ("shell-nohup", ".sh", "nohup xdg-open https://x &\n", "opener command"),
    ("shell-backtick", ".sh", "URL=`xdg-open https://x`\n", "opener command"),
    # 6. Playwright launch shapes.
    ("headless-false-spaced", ".py",
     "b = p.chromium.launch(headless = False)\n", "literal True"),
    ("headless-variable", ".py", "b = p.chromium.launch(headless=flag)\n",
     "literal True"),
    ("launch-alias", ".py",
     "launch = chromium.launch\nb = launch(headless=flag)\n", "alias"),
    ("launch-getattr", ".py",
     "b = getattr(chromium, 'launch')(headless=True)\n", "getattr"),
    ("executable-path-dict-unpack", ".py",
     "b = p.chromium.launch(headless=True, **{'executable_path': '/bin/chrome'})\n",
     "executable_path"),
    ("user-data-dir-kwargs-unpack", ".py",
     "opts = {'user_data_dir': '/home/u'}\n"
     "b = p.chromium.launch(headless=True, **opts)\n",
     "user_data_dir"),
    ("headless-smuggled-in-kwargs", ".py",
     "opts = {'headless': False}\nb = p.chromium.launch(**opts)\n", "literal True"),
    ("user-data-dir-built-incrementally", ".py",
     "opts = {}\n"
     "opts['user_data_dir'] = '/home/u'\n"
     "b = p.chromium.launch(headless=True, **opts)\n",
     "system profile/path"),
    # 7. new_context reached without a direct call.
    ("new-context-getattr", ".py",
     "ctx = getattr(browser, 'new_context')()\n", "new_context"),
    ("new-context-alias", ".py", "helper = browser.new_context\n", "new_context"),
    # 8. suffixes that used to fall outside the scan.
    ("mjs-window-open", ".mjs", 'window.open("https://x");\n', "window.open"),
    ("cjs-open-external", ".cjs",
     'electron.shell.openExternal("https://x");\n', "openExternal"),
    ("htm-target-blank", ".htm", '<a target="_blank">x</a>\n', "_blank"),
    ("vue-target-blank", ".vue",
     '<template><a target="_blank">x</a></template>\n', "_blank"),
    ("svelte-window-open", ".svelte",
     '<a on:click={() => window.open(u)}>x</a>\n', "window.open"),
    ("ps1-start-process", ".ps1", "Start-Process https://x\n", "opener command"),
    # 9. auth SDKs that open the window themselves -- the source never says
    #    "window.open", so only the SDK-name family can see these.
    ("firebase-signin-with-popup", ".html",
     '<script type="module">const c = await signInWithPopup(auth, p);</script>\n',
     "signInWithPopup"),
    ("firebase-link-with-popup", ".js",
     "await linkWithPopup(auth.currentUser, provider);\n", "linkWithPopup"),
    ("firebase-reauthenticate-with-popup", ".js",
     "await reauthenticateWithPopup(user, provider);\n", "reauthenticateWithPopup"),
    ("firebase-popup-import-renamed", ".js",
     'import { signInWithPopup as gp } from "./firebase-auth.js";\n'
     "await gp(auth, provider);\n", "via alias 'gp'"),
    ("firebase-popup-destructured-rename", ".mjs",
     "const { signInWithPopup: go } = mod;\nawait go(auth, provider);\n",
     "via alias 'go'"),
    ("firebase-popup-value-alias", ".js",
     "const go = signInWithPopup;\nawait go(auth, provider);\n", "via alias 'go'"),
    ("msal-login-popup", ".js", "await msal.loginPopup({ scopes });\n", "MSAL"),
    ("msal-acquire-token-popup", ".ts",
     "await client.acquireTokenPopup(request);\n", "MSAL"),
    ("msal-popup-destructured-rename", ".js",
     "const { loginPopup: lp } = msal;\nawait lp({ scopes });\n", "via alias 'lp'"),
    ("gis-token-client-popup", ".js",
     "google.accounts.oauth2.initTokenClient("
     "{ client_id: id, scope: s, ux_mode: 'popup' });\n", "ux_mode"),
    ("gis-code-client-popup-multiline", ".js",
     "google.accounts.oauth2.initCodeClient({\n"
     "  client_id: id,\n  ux_mode: \"popup\",\n});\n", "ux_mode"),
    # ...and the substring backstop still runs for the new suffixes.
    ("mjs-substring-backstop", ".mjs",
     'const s = "webbrowser.open";\n', "forbidden substring"),
    ("svelte-substring-backstop", ".svelte",
     "<!-- headless=False -->\n", "headless=False"),
]


@pytest.mark.parametrize(
    ("suffix", "source", "expected"),
    [row[1:] for row in MUTATIONS],
    ids=[row[0] for row in MUTATIONS],
)
def test_known_bypasses_are_flagged(
    tmp_path: Path, suffix: str, source: str, expected: str
):
    path = tmp_path / f"mutation{suffix}"
    path.write_text(source)
    findings = "\n".join(check_paths([path]))
    assert expected in findings, f"not flagged: {source!r} -> {findings!r}"


# Shapes that legitimately exist in production and must stay quiet.
@pytest.mark.parametrize(
    ("suffix", "source"),
    [
        (".py", "await playwright.chromium.launch(headless=True)\n"),
        (".py", "import subprocess\n"
                "def go(soffice):\n"
                "    subprocess.run([soffice, '--headless', '--convert-to', 'pdf'])\n"),
        (".py", "sid = getattr(session, 'id', '')\n"
                "day = __import__('datetime').date.today()\n"),
        (".html", '<div onclick="if(event.target===this)close()">x</div>\n'),
        (".js", 'const db = indexedDB.open("x");\nel.target = "_self";\n'),
        (".sh", "start_time=$(date)\nnpm run start-server\n"),
        # The sanctioned human-OAuth shape: a current-tab redirect out and a
        # result consumed on the way back in (docs/22).
        (".js", 'import { signInWithRedirect, getRedirectResult } from "./fb.js";\n'
                "await signInWithRedirect(auth, new GoogleAuthProvider());\n"
                "const result = await getRedirectResult(auth);\n"),
        (".js", "google.accounts.oauth2.initTokenClient("
                "{ client_id: id, ux_mode: 'redirect' });\n"),
        (".js", "el.classList.add('popup');\nconst loginPopupHelpText = 'x';\n"),
    ],
    ids=["headless-true", "argv-variable-binary", "getattr-unrelated",
         "target-equality", "indexeddb-open", "shell-lookalike-words",
         "firebase-redirect-flow", "gis-redirect-ux-mode", "popup-lookalike-words"],
)
def test_legitimate_shapes_are_not_flagged(tmp_path: Path, suffix: str, source: str):
    path = tmp_path / f"ok{suffix}"
    path.write_text(source)
    assert check_paths([path]) == []


def test_default_allowlist_is_empty():
    assert guard.ALLOWLIST == {}


def test_allowlist_suppresses_exactly_one_rule_at_one_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    flagged = tmp_path / "aliased.py"
    flagged.write_text("import webbrowser as wb\nwb.open(url)\n")
    other = tmp_path / "other.py"
    other.write_text("import webbrowser as wb\nwb.open(url)\n")
    assert check_paths([flagged]), "fixture must be flagged before allowlisting"

    monkeypatch.setattr(guard, "ALLOWLIST", {
        (flagged.resolve().as_posix(), "webbrowser"):
            "browser-runtime-owner — synthetic fixture, test only",
    })
    assert check_paths([flagged]) == []
    # Same rule at a different path is untouched.
    assert "webbrowser" in "\n".join(check_paths([other]))
    # A different rule at the allowlisted path is untouched.
    flagged.write_text("import webbrowser as wb\nwb.open(url)\nbrowser.new_context()\n")
    assert "new_context" in "\n".join(check_paths([flagged]))


def test_launch_sites_pins_the_single_supervisor_launch():
    sites = guard.launch_sites(guard.production_paths())
    assert len(sites) == 1, sites
    assert sites[0].startswith(guard.RUNTIME_OWNER + ":")


def test_launch_sites_counts_a_rogue_launch_in_any_scanned_suffix(tmp_path: Path):
    rogue_py = tmp_path / "rogue.py"
    rogue_py.write_text("b = chromium.launch(headless=True)\n")
    rogue_js = tmp_path / "rogue.js"
    rogue_js.write_text("const b = await browser.launch(headless=True);\n")
    assert len(guard.launch_sites([rogue_py, rogue_js])) == 2
    # One launch expression counts once, not twice.
    single = tmp_path / "single.py"
    single.write_text("b = playwright.chromium.launch(headless=True)\n")
    assert len(guard.launch_sites([single])) == 1


def test_production_scope_covers_mock_portal_and_new_suffixes():
    assert "mock_portal" in guard.PRODUCTION_ROOTS
    assert {".mjs", ".cjs", ".htm", ".vue", ".svelte", ".ps1"} <= guard.TEXT_SUFFIXES


def test_vendor_and_fixture_names_are_not_blanket_skipped(tmp_path: Path):
    sneaky = tmp_path / "services" / "vendor" / "fixtures" / "bad.js"
    sneaky.parent.mkdir(parents=True)
    sneaky.write_text('window.open("https://x");\n')
    assert "window.open" in "\n".join(check_paths([sneaky]))


def test_repository_passes_its_own_invariants():
    assert check_paths(guard.production_paths()) == []


def test_login_page_signs_in_by_current_tab_redirect_not_a_popup():
    """docs/22: human OAuth may replace this tab, never open a window."""
    login = guard.ROOT / "app" / "static" / "login.html"
    source = login.read_text(encoding="utf-8")
    assert check_paths([login]) == []
    for banned in guard.SDK_POPUP_CALLS:
        assert banned not in source, f"{banned} is back in the sign-in page"
    # The current tab enters the server-owned authorization-code flow. Firebase
    # popup/redirect helpers are both absent, so no cross-origin browser storage
    # is needed to hand the result back.
    assert 'location.assign("/auth/google/start?next="' in source
    assert "signInWithRedirect(" not in source
    assert "getRedirectResult(" not in source
