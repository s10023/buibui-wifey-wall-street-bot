"""Every mechanical `/sanity-check` check, in one run.

The skill carried these as prose plus three embedded shell blocks, so they ran
only when a session remembered to copy them. CLAUDE.md's rule is blunter here
than for `/post-branch`: *a self-check outside CI is not a check* — a script that
only runs by hand eventually reports failure to nobody, for days. So unlike
``post_branch_checks``, everything here is written to be **CI-portable** and the
target runs in the lint workflow.

CI-portability is the whole design constraint, and it is what the prose form got
wrong twice:

* **A gitignored path is absent in CI and present here.** The old §4a block read
  ``config/stocks.json`` directly, so the symbol leg would have crashed in a
  clean checkout; and its ``MISSING`` allowlist was calibrated on a developer
  machine where ``config/youtube_channels.toml`` exists. :func:`check_missing_paths`
  therefore asks git whether a path is *expected* to be absent instead of
  hard-coding the answer, and the symbol leg degrades to skipped.
* **An expectation written in prose rots silently.** The skill claimed "no
  leakage hits" and "exactly these ten MISSING paths". At extraction time the
  real numbers were **3** and **9** — the three leakage hits all legitimate
  (`/db-update` names the parent's systemd unit *as the parent's*, `/ingest-feed`
  names the sibling repo on purpose), so the check had been reporting known-good
  noise. That is the failure the skill itself warns about: a check that is not
  silent when clean gets skimmed.

Every allowlist entry carries its reason inline. An allowlist entry without a
reason is how a check decays into a no-op.

Checks are pure functions over text wherever possible; the git surface is
injected as ``runner`` so the suite can exercise them without a repository.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess  # noqa: S404 - git plumbing, fixed argv, no shell
import sys
import tomllib
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import Path

Runner = Callable[[Sequence[str]], str]

#: Current-state doc surfaces. The dated trees (`docs/audits`, `docs/redesign`,
#: `docs/superpowers`, `docs/plans`) are deliberately absent: a past-tense claim
#: there is correct by construction. Scope is deliberately wider than `.claude/`
#: — `.claude`-only scope is how `docs/system-overview.md` kept saying "Binance
#: Futures" for three months across four post-fork commits.
SURFACE_ROOTS = (".claude",)
SURFACE_FILES = ("CLAUDE.md", "README.md", "docs/system-overview.md")

#: These two files quote the anti-patterns in order to hunt for them, so their
#: own text is not evidence of drift.
SELF_REFERENTIAL = ("sanity-check/SKILL.md", "post-branch/SKILL.md")

#: A sentence that wrapped onto a new line ("...there is no\nmake target.").
MAKE_TARGET_PROSE = frozenset({"target"})

#: Only CODE-FORMATTED invocations count: a backtick, a `$ ` prompt, or line
#: start. Bare prose ("make sense", "make money", "make it visible") is not a
#: claim about a make target and produced 6 of 7 hits when the scope first
#: widened past `.claude/`.
MAKE_RE = re.compile(r"(?:^|`|\$ )make ([a-z][a-z0-9-]+)", re.M)
TIMEFRAME_RES = (
    re.compile(r"--(?:interval|timeframe)s? ([0-9]+[a-z]+)"),
    re.compile(r'TIMEFRAMES?="?([0-9]+[a-z]+)'),
)
STRATEGY_RE = re.compile(r"(?:--strategy |STRATEGY=)([a-z_]+)")
#: The inherited form capped this at `{2,6}`, which reported a 7-character
#: `BTCUSDT` as `symbol=BTCUSD` -- a finding naming something that appears
#: nowhere, so triaging it means grepping for a string that does not exist.
#: The `--symbol `/`SYMBOL=` prefix is what keeps the wider bound tight.
SYMBOL_RE = re.compile(r'(?:--symbols? |SYMBOL="?)([A-Z][A-Z0-9.-]{1,9})')

#: Placeholders in templates, not claims about the tree.
PLACEHOLDER_PREFIXES = ("my_", "<")
PLACEHOLDER_SYMBOLS = frozenset({"SYMBOL", "TF"})

#: Parent-repo artifacts. A hit means a wifey surface presents a crypto-parent
#: artifact as if it were current.
#:
#: ⚠ **Scoped to `.claude/` only, unlike the other checks.** Widening it to
#: CLAUDE.md / README / system-overview immediately returns 4 hits that are all
#: *correct history* — the fork-lineage paragraph, the sister-memory pointer, the
#: README's "forked from" line. That is the prose-marker grep the skill built,
#: measured and rejected: the discriminating signal is not *mentions a crypto
#: artifact* but *presents one as current*, which is semantic. Skills are the
#: right scope because they instruct an action, so a parent artifact there is
#: invocable rather than historical.
LEAKAGE_RE = re.compile(
    r"buibui-moon-trader-bot|`buibui |make buibui-|coins\.json|~/\.claude/skills"
)

#: Files allowed to name parent artifacts, each for a stated reason. Keep this
#: tight: the check's whole value is being silent when clean.
LEAKAGE_EXEMPT: dict[str, str] = {
    "sync-parent/SKILL.md": "addresses the parent repo directly",
    "ingest-video/SKILL.md": "addresses the parent repo directly",
    "post-branch/SKILL.md": "quotes the pattern in order to hunt for it",
    "sanity-check/SKILL.md": "quotes the pattern in order to hunt for it",
    "ingest-feed/SKILL.md": "names the sibling repo path deliberately, as the sibling",
    "db-update/SKILL.md": "names the parent's systemd unit AS the parent's, to disown it",
}

#: `.claude/context/` documents real legacy code paths by design.
LEAKAGE_EXEMPT_DIRS = (".claude/context/",)

PATH_REF_RE = re.compile(
    r"`((?:analytics|signals|utils|web|tools|tests|cli|config|migrations)"
    r"/[A-Za-z0-9_/.]+)`"
)

#: Suffixes that make a dotted tail a FILE rather than a dotted symbol. The bare
#: `[A-Za-z0-9_/.]+` tail accepted any dot, so `analytics/xsmom/replay.replay_targets`
#: -- module plus symbol, the notation a context doc naturally reaches for -- parsed
#: as a path and was reported missing. It was 3 of 3 `missing-paths` hits on the
#: sibling repo, which is the same code against docs that happen to use the notation.
#: A path with NO dot is still accepted, so directory references keep working.
PATH_REF_SUFFIXES = frozenset(
    {
        ".py",
        ".md",
        ".toml",
        ".json",
        ".jsonl",
        ".yaml",
        ".yml",
        ".sh",
        ".txt",
        ".sql",
        ".db",
        ".parquet",
        ".example",
        ".svelte",
        ".ts",
        ".js",
        ".css",
        ".html",
        ".cfg",
        ".ini",
        ".lock",
    }
)


def is_path_like(ref: str) -> bool:
    """Does a matched reference name a file or directory, rather than a symbol?"""
    tail = ref.rsplit("/", 1)[-1]
    if "." not in tail:
        return True
    return any(tail.endswith(suffix) for suffix in PATH_REF_SUFFIXES)


#: Referenced paths that do not exist and must not: each is named *because* it
#: is absent. A path that is merely gitignored is handled by git, not here.
MISSING_PATH_EXEMPT: dict[str, str] = {
    "analytics/indicators_lib.py": "named because it was removed in strat-3",
    "utils/binance_client.py": "named because it was dropped at the fork",
    "utils/binance_client": "same, referenced without the extension",
    "config/coins.json": "the parent's watchlist, named because wifey has none",
    "analytics/forecast/attribution.py": (
        "parent-only, named as the port source for the n_eff deflator"
    ),
    "tools/gate_audit.py": "parent-only, named because it was never ported",
    "cli/card.py": "parent-only, named because it was never ported",
    "config/pundit_roster.toml": "parent-only, named because it was never ported",
    "tests/test_my_strategy.py": "/new-strategy template placeholder",
    "web/ui/src/pages/Foo.svelte": "/frontend-svelte template placeholder",
}

#: Top-level packages that need no `.claude/context/` entry, with the reason.
CONTEXT_EXEMPT: dict[str, str] = {
    "deploy": "documented by its own deploy/README.md",
    "trade": "empty placeholder, both files 0 bytes; knowingly absent",
}

SKIP_DIRS = frozenset({"tests", "docs", "config", "scripts", "__pycache__"})

ROUTER_DIR = Path("web/api/routers")
API_MAIN = Path("web/api/main.py")
SIGNAL_WATCH_GLOB = "config/*signal_watch*.toml"


@dataclass
class Finding:
    """One thing a human must look at. `detail` is printed verbatim."""

    check: str
    detail: str


@dataclass
class CheckResult:
    name: str
    findings: list[Finding] = field(default_factory=list)
    skipped: str | None = None
    #: A leg that could not run, printed but NOT counted. A degraded leg is not
    #: a finding: counting it would make the sweep permanently red in CI, where
    #: the gitignored watchlist is absent by design, and a check that is never
    #: green stops being read.
    note: str | None = None


def _run(argv: Sequence[str]) -> str:
    try:
        out = subprocess.run(  # noqa: S603 - fixed argv, no shell
            list(argv), capture_output=True, text=True, check=False
        )
    except OSError:
        return ""
    return out.stdout


def surface_paths() -> list[Path]:
    """Every current-state doc surface that exists, self-referential ones dropped."""
    paths: list[Path] = []
    for root in SURFACE_ROOTS:
        paths += sorted(Path(root).rglob("*.md"))
    paths += [Path(p) for p in SURFACE_FILES]
    return [
        p
        for p in paths
        if p.exists() and not any(str(p).endswith(s) for s in SELF_REFERENTIAL)
    ]


def makefile_targets(makefile: str) -> set[str]:
    """Target names from a Makefile's own text."""
    return set(re.findall(r"^([a-zA-Z][\w-]*):", makefile, re.M))


def check_fork_drift(
    surfaces: Iterable[tuple[str, str]],
    targets: set[str],
    timeframes: set[str],
    strategies: set[str],
    symbols: set[str] | None,
) -> list[Finding]:
    """Invocable artifacts named by a doc but absent from the code.

    Keyed to **invocable** things — make targets, timeframes, `--strategy` and
    `SYMBOL` flags — because those are falsifiable. A prose-marker grep for
    `binance|BTCUSDT|…` was built, measured and rejected: over these surfaces it
    returns ~40 hits that are almost all correct history, and the discriminating
    signal is *presented as current*, which is semantic. `symbols=None` skips the
    symbol leg, which is what happens wherever the gitignored watchlist is absent.
    """
    out: list[Finding] = []
    for name, text in surfaces:
        for m in MAKE_RE.finditer(text):
            target = m.group(1)
            # a trailing '-' means a glob/placeholder ("make wifey-*")
            if target.endswith("-") or target in MAKE_TARGET_PROSE or target in targets:
                continue
            out.append(Finding("fork-drift", f"{name}: make-target={target}"))
        for pattern in TIMEFRAME_RES:
            out += [
                Finding("fork-drift", f"{name}: timeframe={m.group(1)}")
                for m in pattern.finditer(text)
                if m.group(1) not in timeframes
            ]
        for m in STRATEGY_RE.finditer(text):
            value = m.group(1)
            if value not in strategies and not value.startswith(PLACEHOLDER_PREFIXES):
                out.append(Finding("fork-drift", f"{name}: strategy={value}"))
        if symbols is not None:
            for m in SYMBOL_RE.finditer(text):
                #: A trailing dot or dash is never part of a ticker, but the
                #: capture class admits both -- so an argparse metavar
                #: `--symbols SYM...` captured `SYM...`, which can never match
                #: `PLACEHOLDER_SYMBOLS` and so was permanently unsuppressable.
                value = m.group(1).rstrip(".-")
                if value not in symbols and value not in PLACEHOLDER_SYMBOLS:
                    out.append(Finding("fork-drift", f"{name}: symbol={value}"))
    return out


def check_parent_leakage(surfaces: Iterable[tuple[str, str]]) -> list[Finding]:
    """A skill presenting a crypto-parent artifact as current.

    Narrower scope than its siblings on purpose — see `LEAKAGE_RE`.
    """
    out: list[Finding] = []
    for name, text in surfaces:
        if not name.startswith(".claude/"):
            continue
        if any(name.endswith(s) for s in LEAKAGE_EXEMPT):
            continue
        if any(d in name for d in LEAKAGE_EXEMPT_DIRS):
            continue
        for lineno, line in enumerate(text.splitlines(), start=1):
            if LEAKAGE_RE.search(line):
                out.append(
                    Finding("parent-leakage", f"{name}:{lineno}: {line.strip()}")
                )
    return out


def check_missing_paths(
    surfaces: Iterable[tuple[str, str]],
    exists: Callable[[str], bool],
    ignored: Callable[[str], bool],
) -> list[Finding]:
    """Repo paths a doc names in backticks that are not there.

    A path is fine when it exists, when git ignores it (so a clean checkout is
    *expected* not to have it — this is what makes the check CI-portable), or
    when it is on the allowlist because the doc names it precisely for being
    gone.
    """
    seen: set[str] = set()
    out: list[Finding] = []
    for name, text in surfaces:
        for m in PATH_REF_RE.finditer(text):
            path = m.group(1)
            if path in seen or path in MISSING_PATH_EXEMPT:
                continue
            if not is_path_like(path):
                continue
            if exists(path) or ignored(path):
                continue
            seen.add(path)
            out.append(Finding("missing-paths", f"{name}: MISSING {path}"))
    return out


def check_context_coverage(packages: Iterable[str], context_blob: str) -> list[Finding]:
    """Every top-level package reaches `.claude/context/`.

    A presence check rather than a diff-keyed one: the `/post-branch` greps key
    off *changed* files, so they cannot detect a package the docs have never
    heard of.
    """
    out: list[Finding] = []
    for pkg in sorted(packages):
        if pkg in CONTEXT_EXEMPT:
            continue
        if not re.search(rf"\b{re.escape(pkg)}\b", context_blob):
            out.append(Finding("context-coverage", f"UNDOCUMENTED package: {pkg}/"))
    return out


def check_router_wiring(
    on_disk: Iterable[str], imported: Iterable[str], registered: Iterable[str]
) -> list[Finding]:
    """Three hand-maintained lists of the same routers must agree.

    `web/api/main.py` names each router twice — once in a `from … import (…)`
    tuple and once in the `for module in (…)` loop — and neither is derived from
    the directory. A router missing from the loop imports cleanly and serves
    nothing: a 404 rather than an error, which is the silent shape.
    """
    disk, imp, reg = set(on_disk), set(imported), set(registered)
    out: list[Finding] = []
    for name in sorted(disk - imp):
        out.append(
            Finding("router-wiring", f"{name}: on disk, not imported by main.py")
        )
    for name in sorted(imp - reg):
        out.append(Finding("router-wiring", f"{name}: imported but never registered"))
    for name in sorted(reg - disk):
        out.append(
            Finding("router-wiring", f"{name}: registered but no module on disk")
        )
    return out


def check_config_strategies(
    configs: Iterable[tuple[str, dict[str, object]]], strategies: set[str]
) -> list[Finding]:
    """Every `[strategy_params.X]` key names a real strategy.

    A stale key is inert rather than loud: nothing dispatches it, so its settings
    silently govern nothing.
    """
    out: list[Finding] = []
    for name, data in configs:
        params = data.get("strategy_params")
        if not isinstance(params, dict):
            continue
        for key in sorted(params):
            if key not in strategies:
                out.append(
                    Finding(
                        "config-strategies",
                        f"{name}: [strategy_params.{key}] is not a strategy",
                    )
                )
    return out


def check_cli_documented(subcommands: Iterable[str], readme: str) -> list[Finding]:
    """Every `wifey` subcommand appears in README.

    Generated from argparse rather than compared against a written list — the
    parent's equivalent list sat stale for months, because a hand-written command
    list is exactly the surface that rots without any signal.
    """
    return [
        Finding("cli-documented", f"`wifey {name}` is not mentioned in README.md")
        for name in sorted(subcommands)
        if not re.search(rf"\b{re.escape(name)}\b", readme)
    ]


def _read_surfaces() -> list[tuple[str, str]]:
    return [(str(p), p.read_text(encoding="utf-8")) for p in surface_paths()]


def _top_level_packages() -> list[str]:
    return [
        p.name
        for p in Path().iterdir()
        if p.is_dir() and p.name not in SKIP_DIRS and not p.name.startswith(".")
    ]


def _router_lists() -> tuple[list[str], list[str], list[str]]:
    on_disk = [p.stem for p in ROUTER_DIR.glob("*.py") if p.stem != "__init__"]
    text = API_MAIN.read_text(encoding="utf-8")
    import_block = re.search(r"from web\.api\.routers import \(([^)]*)\)", text)
    loop_block = re.search(r"for module in \(([^)]*)\)", text)
    imported = re.findall(r"\w+", import_block.group(1)) if import_block else []
    registered = re.findall(r"\w+", loop_block.group(1)) if loop_block else []
    return on_disk, imported, registered


def _git_ignored(runner: Runner) -> Callable[[str], bool]:
    def ignored(path: str) -> bool:
        return bool(runner(["git", "check-ignore", path]).strip())

    return ignored


def _load_code_facts() -> tuple[set[str], set[str]] | None:
    """(strategies, timeframes) from the code, or None if imports are unavailable.

    Degrading rather than raising is what lets the sweep run in CI's markdown
    job, which installs nothing. The legs that need these facts skip; the four
    pure-text legs — the ones that actually catch doc drift — still run. Without
    this the gate would sit behind a `**/*.py` path filter and therefore never
    run on a docs-only PR, i.e. on the exact change it exists to catch.
    """
    try:
        from analytics.data_fetcher import _INTERVAL_CONFIG
        from analytics.strategies._registry import STRATEGY_REGISTRY
    except Exception:  # noqa: BLE001 - any import failure degrades identically
        return None
    return set(STRATEGY_REGISTRY), set(_INTERVAL_CONFIG)


def gather(runner: Runner = _run) -> list[CheckResult]:
    """Run every check against the working tree. Order matches the skill."""
    surfaces = _read_surfaces()
    facts = _load_code_facts()
    no_deps = "project dependencies are not installed"

    if facts is None:
        fork_drift = CheckResult("fork-drift", skipped=no_deps)
        config_strategies = CheckResult("config-strategies", skipped=no_deps)
    else:
        strategies, timeframes = facts
        targets = makefile_targets(Path("Makefile").read_text(encoding="utf-8"))

        watchlist = Path("config/stocks.json")
        symbols: set[str] | None = None
        if watchlist.exists():
            symbols = set(json.loads(watchlist.read_text(encoding="utf-8")))

        fork_drift = CheckResult(
            "fork-drift",
            check_fork_drift(surfaces, targets, timeframes, strategies, symbols),
        )
        if symbols is None:
            fork_drift.note = (
                "symbol leg skipped: config/stocks.json is absent (gitignored)"
            )

        configs: list[tuple[str, dict[str, object]]] = [
            (str(cfg), tomllib.loads(cfg.read_text(encoding="utf-8")))
            for cfg in sorted(Path().glob(SIGNAL_WATCH_GLOB))
        ]
        config_strategies = CheckResult(
            "config-strategies", check_config_strategies(configs, strategies)
        )

    subcommands = _cli_subcommands()
    if subcommands is None:
        cli_documented = CheckResult("cli-documented", skipped=no_deps)
    else:
        readme = Path("README.md").read_text(encoding="utf-8")
        cli_documented = CheckResult(
            "cli-documented", check_cli_documented(subcommands, readme)
        )

    on_disk, imported, registered = _router_lists()

    return [
        fork_drift,
        CheckResult("parent-leakage", check_parent_leakage(surfaces)),
        CheckResult(
            "missing-paths",
            check_missing_paths(
                surfaces, lambda p: Path(p).exists(), _git_ignored(runner)
            ),
        ),
        CheckResult(
            "context-coverage",
            check_context_coverage(_top_level_packages(), _read_context_blob()),
        ),
        CheckResult(
            "router-wiring", check_router_wiring(on_disk, imported, registered)
        ),
        config_strategies,
        cli_documented,
    ]


def _read_context_blob() -> str:
    root = Path(".claude/context")
    if not root.exists():
        return ""
    return "\n".join(p.read_text(encoding="utf-8") for p in sorted(root.rglob("*.md")))


def subcommand_names(parser: argparse.ArgumentParser) -> set[str]:
    """Every subcommand an argparse tree accepts, read from its own actions."""
    names: set[str] = set()
    for action in parser._actions:  # noqa: SLF001 - argparse exposes no public accessor
        choices = getattr(action, "choices", None)
        if isinstance(choices, dict):
            names |= set(choices)
    return names


def _cli_subcommands() -> list[str] | None:
    """Subcommand names straight from the argparse tree, never a written list.

    None when the CLI package cannot be imported — see :func:`_load_code_facts`.
    """
    try:
        from cli.main import build_parser
    except Exception:  # noqa: BLE001 - any import failure degrades identically
        return None
    return sorted(subcommand_names(build_parser()))


def render(results: Sequence[CheckResult]) -> tuple[list[str], int]:
    out = ["sanity_checks — mechanical sweep", ""]
    total = 0
    for r in results:
        if r.skipped:
            out.append(f"  {r.name:<18} SKIPPED  ({r.skipped})")
            continue
        if not r.findings:
            out.append(f"  {r.name:<18} clean")
            if r.note:
                out.append(f"      note: {r.note}")
            continue
        total += len(r.findings)
        out.append(f"  {r.name:<18} {len(r.findings)} to triage")
        if r.note:
            out.append(f"      note: {r.note}")
        for f in r.findings:
            out.append(f"      {f.detail}")
    out += [
        "",
        f"  {total} finding(s). Every allowlist entry carries its reason inline —",
        "  an entry without one is how a check decays into a no-op.",
    ]
    return out, total


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="sanity_checks",
        description="Every mechanical /sanity-check check, in one run.",
    )
    parser.add_argument("--check", help="run only this named check")
    parser.add_argument(
        "--exit-zero",
        action="store_true",
        help="always exit 0 (findings are advisory, not a gate)",
    )
    args = parser.parse_args(argv)

    results = gather()
    if args.check:
        results = [r for r in results if r.name == args.check]
        if not results:
            print(f"sanity_checks: no such check: {args.check}", file=sys.stderr)
            return 2
    lines, total = render(results)
    for line in lines:
        print(line)
    return 0 if (args.exit_zero or total == 0) else 1


if __name__ == "__main__":
    raise SystemExit(main())
