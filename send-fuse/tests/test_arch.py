"""Architecture guards (the code constitution, made executable).

send-fuse must stay: one file, standard library only, unable to send anything itself,
with environment access in exactly one function and every concept defined once."""
import ast
import sys
import unittest
from pathlib import Path

SRC = Path(__file__).resolve().parent.parent / "sendfuse.py"
TREE = ast.parse(SRC.read_text("utf-8"))
NETWORK = {"smtplib", "socket", "ssl", "http", "urllib", "imaplib", "poplib", "ftplib", "requests", "httpx",
           "asyncio", "email.mime"}


def imports():
    for n in ast.walk(TREE):
        if isinstance(n, ast.Import):
            yield from (a.name for a in n.names)
        elif isinstance(n, ast.ImportFrom) and n.module:
            yield n.module


class Architecture(unittest.TestCase):
    @unittest.skipUnless(hasattr(sys, "stdlib_module_names"), "needs Python 3.10+ (sys.stdlib_module_names)")
    def test_stdlib_only(self):
        bad = [m for m in imports() if m.split(".")[0] not in sys.stdlib_module_names]
        self.assertEqual(bad, [])

    def test_cannot_send_anything_itself(self):
        bad = [m for m in imports() if m.split(".")[0] in NETWORK or m in NETWORK]
        self.assertEqual(bad, [], "send-fuse must never be able to send or fetch over the network")

    def test_environment_read_in_one_place(self):
        users = set()
        for fn in [n for n in ast.walk(TREE) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]:
            for n in ast.walk(fn):
                if isinstance(n, ast.Attribute) and n.attr in ("environ", "getenv", "expandvars") \
                        and isinstance(n.value, ast.Name) and n.value.id in ("os", "path"):
                    users.add(fn.name)
        self.assertEqual(users, {"getenv"})

    def test_under_size_limit(self):
        self.assertLess(len(SRC.read_text("utf-8").splitlines()), 1500)

    def test_each_concept_defined_once(self):
        names = [n.name for n in TREE.body if isinstance(n, (ast.ClassDef, ast.FunctionDef))]
        dupes = {n for n in names if names.count(n) > 1}
        self.assertEqual(dupes, set())

    def test_no_copy_pasted_functions(self):
        """Same AST shape (ignoring names and literals) twice = the same thing written twice."""
        def shape(fn):
            def norm(n):
                for c in ast.walk(n):
                    for attr in ("id", "arg", "attr", "name"):
                        if hasattr(c, attr):
                            setattr(c, attr, "_")
                    if isinstance(c, ast.Constant):
                        c.value = 0
                return ast.dump(n, include_attributes=False)
            return norm(ast.parse(ast.unparse(fn)).body[0])
        fns = [n for n in ast.walk(TREE) if isinstance(n, ast.FunctionDef) and len(n.body) >= 4]
        seen, dupes = {}, []
        for f in fns:
            k = shape(f)
            if k in seen:
                dupes.append((seen[k], f.name))
            seen[k] = f.name
        self.assertEqual(dupes, [])

    def test_every_rule_documented_in_readme(self):
        sys.path.insert(0, str(SRC.parent))
        import sendfuse
        readme = (SRC.parent / "README.md").read_text("utf-8")
        missing = [r for r in sendfuse.RULE_NAMES if f"`{r}`" not in readme]
        self.assertEqual(missing, [])
