#!/usr/bin/env python3
"""
test_home_runtime_lib_closure.py — PROJ-039/T-011 C2-v2.1b.

Guards the slim resident-lib invariant for self-contained agent home repos. Two
defects in C2-v2.1 motivated this: scaffold.sh over-copied the WHOLE agent-tooling
lib/ (26 modules) into every home repo's .claude/lib/, shipping workstation/Hermes
-only code (the PROJ-038 decay detector, C1 one-shots, harnesses, reporting) that
never runs in a home repo.

The fix: scaffold.sh / sync-agent-tooling.sh copy ONLY hooks/home-runtime-lib.manifest
— the transitive import closure of the shipped home-repo hooks. This test makes the
manifest enforced + repeatable rather than hand-curated:

  1. test_manifest_matches_closure — recompute the closure from the hooks scaffold
     actually ships and assert it equals the manifest. FAILS on re-bloat (a manifest
     module nothing imports) AND on a missing dependency (an imported module absent
     from the manifest). This is the "fail if any .claude/lib module is unreferenced"
     guard from the brief.

  2. test_scaffold_copies_only_closure — scaffold a home repo and assert its
     .claude/lib is exactly the manifest set, each file byte-identical to
     agent-tooling/lib, with NO out-of-closure module present (e.g. decay/).
"""

import ast
import subprocess
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
LIB_DIR = REPO_ROOT / "lib"
MANIFEST = REPO_ROOT / "hooks" / "home-runtime-lib.manifest"
SCAFFOLD = REPO_ROOT / "scaffold.sh"


def read_manifest() -> set[str]:
    """Manifest entries (paths relative to lib/), comments/blanks stripped."""
    entries = set()
    for line in MANIFEST.read_text().splitlines():
        entry = line.split("#", 1)[0].strip()
        if entry:
            entries.add(entry)
    return entries


def _local_modules() -> dict[str, Path]:
    """Map dotted lib module name -> file path for every module under lib/."""
    mods = {}
    for p in LIB_DIR.rglob("*.py"):
        dotted = ".".join(p.relative_to(LIB_DIR).with_suffix("").parts)
        mods[dotted] = p
    return mods


def _lib_imports(path: Path, local: dict[str, Path], pkg_parts: list[str]) -> set[str]:
    """lib modules imported by `path` — handles `from lib import X`, `from lib.x
    import y`, and intra-package relative `from . import x` / `from .x import y`."""
    out: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, ast.ImportFrom):
            if node.level:  # relative (inside the lib package)
                base = pkg_parts
                if node.module:
                    cand = ".".join(base + [node.module])
                    if cand in local:
                        out.add(cand)
                    else:
                        for n in node.names:
                            c = ".".join(base + [node.module, n.name])
                            if c in local:
                                out.add(c)
                else:
                    for n in node.names:
                        c = ".".join(base + [n.name])
                        if c in local:
                            out.add(c)
            else:
                mod = node.module or ""
                m = mod[4:] if mod.startswith("lib.") else mod
                if m in local:
                    out.add(m)
                elif mod == "lib":
                    for n in node.names:
                        if n.name in local:
                            out.add(n.name)
        elif isinstance(node, ast.Import):
            for n in node.names:
                if n.name.startswith("lib.") and n.name[4:] in local:
                    out.add(n.name[4:])
    return out


def closure_from_hooks(hook_py_files: list[Path]) -> set[str]:
    """Transitive lib import closure (dotted module names) reachable from the
    given hook entry points."""
    local = _local_modules()
    roots: set[str] = set()
    for hp in hook_py_files:
        roots |= _lib_imports(hp, local, pkg_parts=[])
    seen: set[str] = set()
    stack = list(roots)
    while stack:
        m = stack.pop()
        if m in seen:
            continue
        seen.add(m)
        pkg_parts = m.split(".")[:-1]
        for dep in _lib_imports(local[m], local, pkg_parts):
            if dep not in seen:
                stack.append(dep)
    return seen


def _scaffold(target: Path) -> None:
    subprocess.run(
        ["bash", str(SCAFFOLD), "podzone", "closuretest", "coder",
         "--target-dir", str(target), "--force"],
        cwd=str(REPO_ROOT), check=True, capture_output=True, text=True,
    )


def _dotted_to_relpath(dotted: str) -> str:
    return dotted.replace(".", "/") + ".py"


class TestHomeRuntimeLibClosure(unittest.TestCase):

    def test_manifest_exists_and_nonempty(self):
        self.assertTrue(MANIFEST.is_file(), f"missing manifest: {MANIFEST}")
        self.assertTrue(read_manifest(), "manifest is empty")

    def test_manifest_matches_closure(self):
        """The manifest must equal the closure of the hooks scaffold actually
        ships (coder = maximal hook set) plus the lib package __init__.py.
        Mismatch in either direction fails: re-bloat or a missing dependency."""
        with tempfile.TemporaryDirectory() as td:
            target = Path(td) / "home"
            _scaffold(target)
            hook_py = sorted((target / ".claude" / "hooks").glob("*.py"))
            self.assertTrue(hook_py, "scaffold shipped no python hooks")
            closure = closure_from_hooks(hook_py)

        # lib package needs __init__.py to import; the closure of *modules* does
        # not name it, so add it explicitly to form the copy set.
        expected = {_dotted_to_relpath(m) for m in closure} | {"__init__.py"}
        manifest = read_manifest()

        bloat = manifest - expected
        missing = expected - manifest
        self.assertEqual(
            manifest, expected,
            f"\nmanifest != shipped-hook closure."
            f"\n  re-bloat (in manifest, unreferenced): {sorted(bloat)}"
            f"\n  missing  (imported, not in manifest): {sorted(missing)}"
            f"\n  -> update hooks/home-runtime-lib.manifest to match the closure.",
        )

    def test_scaffold_copies_only_closure(self):
        """A scaffolded home repo's .claude/lib must be exactly the manifest set,
        each byte-identical to agent-tooling/lib, with no out-of-closure module."""
        manifest = read_manifest()
        with tempfile.TemporaryDirectory() as td:
            target = Path(td) / "home"
            _scaffold(target)
            lib = target / ".claude" / "lib"

            present = {
                str(p.relative_to(lib))
                for p in lib.rglob("*")
                if p.is_file() and "__pycache__" not in p.parts
            }
            self.assertEqual(
                present, manifest,
                f"\nscaffolded .claude/lib != manifest set."
                f"\n  extra (out of closure): {sorted(present - manifest)}"
                f"\n  missing:                {sorted(manifest - present)}",
            )

            # decay/ (PROJ-038) is the canonical over-copy regression — assert gone.
            self.assertFalse(
                (lib / "decay").exists(),
                "decay/ subtree must not be copied into a home repo",
            )

            for rel in manifest:
                src = LIB_DIR / rel
                dst = lib / rel
                self.assertTrue(dst.is_file(), f"manifest module not copied: {rel}")
                self.assertEqual(
                    src.read_bytes(), dst.read_bytes(),
                    f"lib/{rel} not byte-identical to agent-tooling/lib",
                )


if __name__ == "__main__":
    unittest.main()
