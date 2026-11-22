"""The five checks, on strings (no git): what blocks, what only notes, and — just as important — what must stay quiet."""
import sys
import textwrap
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import bar_lock as bl  # noqa: E402

CFG = bl.DEFAULT_CONFIG


def d(s: str) -> str:
    return textwrap.dedent(s).lstrip("\n")


def run(before, after, path="tests/test_total.py", cfg=None, extra=()):
    changes = [bl.FileChange(path, None if before is None else d(before), None if after is None else d(after))]
    changes += [bl.FileChange(p, None if b is None else d(b), None if a is None else d(a)) for p, b, a in extra]
    return bl.analyze(changes, cfg or CFG)


def blocks(found):
    return sorted(f.code for f in found if f.level == "block")


def notes(found):
    return sorted(f.code for f in found if f.level == "note")


BASE = """
    import math
    from decimal import Decimal

    def test_total():
        total = compute()
        assert total == Decimal("19.99")
        assert math.isclose(price, 9.99, rel_tol=1e-9)

    def test_other():
        assert other() == 3
"""


class K1Assertions(unittest.TestCase):
    def test_specific_assertion_replaced_by_truthiness(self):
        after = BASE.replace('assert total == Decimal("19.99")', "assert total")
        self.assertEqual(blocks(run(BASE, after)), ["K1.weakened"])

    def test_always_true_assertion(self):
        after = BASE.replace('assert total == Decimal("19.99")', "assert True")
        self.assertIn("K1.trivial", blocks(run(BASE, after)))

    def test_unittest_style_weakened(self):
        b = "class T:\n    def test_a(self):\n        self.assertEqual(total, 5)\n"
        a = "class T:\n    def test_a(self):\n        self.assertTrue(total)\n"
        self.assertEqual(blocks(run(b, a)), ["K1.weakened"])

    def test_assertion_deleted(self):
        after = BASE.replace('        assert total == Decimal("19.99")\n', "")
        self.assertEqual(blocks(run(BASE, after)), ["K1.removed"])

    def test_edited_expected_value_is_only_a_note(self):
        after = BASE.replace("19.99", "18.5")
        found = run(BASE, after)
        self.assertEqual(blocks(found), [])
        self.assertEqual(notes(found), ["K1.edited"])

    def test_assertion_moved_to_a_helper_is_not_removal(self):
        after = """
            import math
            from decimal import Decimal

            def _check(total):
                assert total == Decimal("19.99")

            def test_total():
                total = compute()
                _check(total)
                assert math.isclose(price, 9.99, rel_tol=1e-9)

            def test_other():
                assert other() == 3
        """
        self.assertEqual(blocks(run(BASE, after)), [])

    def test_parametrize_consolidation_is_not_removal(self):
        b = "def test_a():\n    assert f(1) == 2\n    assert f(2) == 4\n    assert f(3) == 6\n"
        a = ("import pytest\n@pytest.mark.parametrize('x,y', [(1, 2), (2, 4), (3, 6)])\n"
             "def test_a(x, y):\n    assert f(x) == y\n")
        self.assertEqual(blocks(run(b, a)), [])

    def test_loop_consolidation_is_not_removal(self):
        b = "def test_a():\n    assert f(1) == 2\n    assert f(2) == 4\n"
        a = "def test_a():\n    for x, y in [(1, 2), (2, 4)]:\n        assert f(x) == y\n"
        self.assertEqual(blocks(run(b, a)), [])

    def test_a_comparison_helper_call_replacing_equality_is_not_a_loss(self):
        b = "def test_a():\n    assert isinstance(w, T)\n    assert w.scale == 3.0\n    assert w.layout == 'fp8'\n"
        a = "def test_a():\n    assert torch.equal(w.view(1), ref.view(1))\n    assert s.item() == 3.0\n    assert m.w.layout == 'fp8'\n"
        self.assertEqual(blocks(run(b, a)), [])

    def test_equal_call_turned_into_plain_truthiness_is_weakened(self):
        b = "def test_a():\n    assert check(x) == 5\n"
        a = "def test_a():\n    assert check(x)\n"
        self.assertEqual(blocks(run(b, a)), ["K1.weakened"])

    def test_equivalent_style_swap_is_fine(self):
        b = "class T:\n    def test_a(self):\n        self.assertEqual(total, 5)\n"
        a = "def test_a():\n    assert total == 5\n"
        self.assertEqual(blocks(run(b, a)), [])

    def test_adding_assertions_is_fine(self):
        after = d(BASE) + "\ndef test_new():\n    assert 1 + 1 == 2\n"
        self.assertEqual(run(BASE, after), [])

    def test_deleted_test_file_with_no_trace_blocks(self):
        self.assertEqual(blocks(run(BASE, None)), ["K1.file_deleted"])

    def test_test_file_split_across_files_is_not_a_deletion(self):
        found = run(BASE, None, extra=[("tests/test_other.py", None, BASE)])
        self.assertEqual(blocks(found), [])

    def test_tests_deleted_together_with_the_code_they_test_are_a_note(self):
        found = run(BASE, None, path="app/tests/test_total.py", extra=[("app/total.py", "def compute():\n    pass\n", None)])
        self.assertEqual((blocks(found), notes(found)), ([], ["K1.file_deleted_with_code"]))
        lone = run(BASE, None, path="app/tests/test_total.py", extra=[("other/x.py", "x = 1\n", None)])
        self.assertEqual(blocks(lone), ["K1.file_deleted"])

    def test_tests_deleted_with_the_module_they_import_are_a_note_wherever_it_lives(self):
        test = "from pkg.mapper import convert\ndef test_a():\n    assert convert(1) == 2\n"
        found = run(test, None, path="tests-unit/mapper_test.py", extra=[("pkg/mapper.py", "def convert(x):\n    return x\n", None)])
        self.assertEqual((blocks(found), notes(found)), ([], ["K1.file_deleted_with_code"]))
        kept = run(test, None, path="tests-unit/mapper_test.py", extra=[("pkg/mapper.py", "x = 1\n", "x = 2\n")])
        self.assertEqual(blocks(kept), ["K1.file_deleted"])        # the code is still there: the test just vanished

    def test_deleted_function_is_only_a_note(self):
        after = BASE.split("def test_other")[0]
        found = run(BASE, after)
        self.assertEqual(blocks(found), [])
        self.assertEqual(notes(found), ["K1.func_deleted"])

    def test_renamed_so_it_no_longer_runs(self):
        after = BASE.replace("def test_other", "def _test_other")
        found = run(BASE, after)
        self.assertEqual(blocks(found), ["K1.renamed_off"])

    def test_non_test_file_is_not_looked_at(self):
        self.assertEqual(run("def f():\n    assert x == 1\n", "def f():\n    pass\n", path="src/app.py"), [])

    def test_unparsable_file_says_so(self):
        found = run(BASE, "def test_x(:\n")
        self.assertEqual(notes(found), ["K0.parse"])
        self.assertEqual(blocks(found), [])


class K2Disabled(unittest.TestCase):
    def check(self, line: str, expect_block: bool, head="import pytest, sys, unittest\n"):
        b = head + "def test_a():\n    assert f() == 1\n"
        a = head + line + "\ndef test_a():\n    assert f() == 1\n"
        self.assertEqual(blocks(run(b, a)), ["K2.skip"] if expect_block else [], line)

    def test_unconditional_markers_block(self):
        for line in ('@pytest.mark.skip(reason="flaky")', "@pytest.mark.skip", "@pytest.mark.xfail",
                     '@pytest.mark.xfail(reason="bug")', '@unittest.skip("x")', "@unittest.expectedFailure",
                     '@pytest.mark.skipif(True, reason="x")', '@unittest.skipUnless(False, "x")',
                     '@unittest.skipIf(True, "x")'):
            self.check(line, True)

    def test_environment_guards_are_fine(self):
        for line in ('@pytest.mark.skipif(sys.platform == "win32", reason="x")',
                     '@pytest.mark.xfail(sys.version_info < (3, 9), reason="x")',
                     '@pytest.mark.xfail(condition=sys.platform == "win32")',
                     '@unittest.skipUnless(sys.platform == "linux", "x")', "@pytest.mark.slow"):
            self.check(line, False)

    def test_skip_call_at_top_of_body_blocks_but_inside_if_does_not(self):
        b = "import pytest, sys\ndef test_a():\n    assert f() == 1\n"
        bare = "import pytest, sys\ndef test_a():\n    pytest.skip('later')\n    assert f() == 1\n"
        guarded = "import pytest, sys\ndef test_a():\n    if sys.platform == 'win32':\n        pytest.skip('x')\n    assert f() == 1\n"
        self.assertEqual(blocks(run(b, bare)), ["K2.skip"])
        self.assertEqual(blocks(run(b, guarded)), [])

    def test_class_level_and_module_level(self):
        b = "import pytest\nclass TestA:\n    def test_a(self):\n        assert f() == 1\n"
        cls = "import pytest\n@pytest.mark.skip\nclass TestA:\n    def test_a(self):\n        assert f() == 1\n"
        mod = "import pytest\npytestmark = pytest.mark.skip(reason='x')\nclass TestA:\n    def test_a(self):\n        assert f() == 1\n"
        self.assertEqual(blocks(run(b, cls)), ["K2.skip"])
        self.assertEqual(blocks(run(b, mod)), ["K2.skip"])

    def test_removing_or_keeping_a_skip_is_fine(self):
        with_skip = "import pytest\n@pytest.mark.skip\ndef test_a():\n    assert f() == 1\n"
        without = "import pytest\ndef test_a():\n    assert f() == 1\n"
        self.assertEqual(blocks(run(with_skip, without)), [])
        self.assertEqual(blocks(run(with_skip, with_skip + "\nX = 1\n")), [])

    def test_brand_new_test_that_is_already_skipped_is_a_note(self):
        found = run("def test_a():\n    assert 1 == 1\n",
                    "import pytest\ndef test_a():\n    assert 1 == 1\n@pytest.mark.skip\ndef test_b():\n    assert 2 == 2\n")
        self.assertEqual(blocks(found), [])
        self.assertEqual(notes(found), ["K2.skip_new"])


class K4Swallowed(unittest.TestCase):
    def test_broad_except_with_pass_blocks(self):
        b = "def test_a():\n    assert f() == 1\n"
        for body in ("try:\n        f()\n    except Exception:\n        pass", "try:\n        f()\n    except:\n        pass",
                     "try:\n        f()\n    except (ValueError, AssertionError):\n        ...",
                     "with contextlib.suppress(Exception):\n        f()"):
            a = "import contextlib\ndef test_a():\n    assert f() == 1\n    " + body + "\n"
            self.assertEqual(blocks(run(b, a)), ["K4.swallow"], body)

    def test_a_swallow_that_was_already_there_is_not_ours(self):
        src = "def test_a():\n    try:\n        f()\n    except Exception:\n        pass\n    assert f() == 1\n"
        self.assertEqual(run(src, src + "\ndef test_b():\n    assert g() == 2\n"), [])

    def test_narrow_or_logged_handlers_are_fine(self):
        b = "def test_a():\n    assert f() == 1\n"
        for body in ("try:\n        f()\n    except ValueError:\n        pass",
                     "try:\n        f()\n    except Exception as e:\n        log(e)"):
            a = "def test_a():\n    assert f() == 1\n    " + body + "\n"
            self.assertEqual(blocks(run(b, a)), [], body)

    def test_helpers_in_the_tests_folder_may_swallow_errors(self):
        b = "def load(p):\n    return read(p)\n"
        a = "def load(p):\n    try:\n        return read(p)\n    except Exception:\n        pass\n"
        self.assertEqual(blocks(run(b, a, path="tests/helpers.py")), [])

    def test_gate_files_count_too(self):
        cfg = {**CFG, "gate_globs": ["scripts/gate.py"]}
        b = "def check():\n    return run()\n"
        a = "def check():\n    try:\n        return run()\n    except Exception:\n        pass\n"
        self.assertEqual(blocks(run(b, a, path="scripts/gate.py", cfg=cfg)), ["K4.swallow"])
        self.assertEqual(run(b, a, path="scripts/other.py", cfg=cfg), [])


class JavaScript(unittest.TestCase):
    B = "describe('x', () => {\n  it('adds', () => {\n    expect(add(1, 2)).toBe(3);\n    expect(sub(3, 1)).toBe(2);\n  });\n});\n"

    def js(self, after, path="src/add.test.ts"):
        return run(self.B, after, path=path)

    def test_weakened(self):
        self.assertEqual(blocks(self.js(self.B.replace("toBe(3)", "toBeTruthy()"))), ["K1.weakened"])

    def test_trivial(self):
        self.assertIn("K1.trivial", blocks(self.js(self.B.replace("expect(add(1, 2)).toBe(3)", "expect(true).toBe(true)"))))

    def test_removed(self):
        self.assertEqual(blocks(self.js(self.B.replace("    expect(sub(3, 1)).toBe(2);\n", ""))), ["K1.removed"])

    def test_skip_only_and_x_variants(self):
        for repl in ("it.skip('adds'", "xit('adds'", "it.only('adds'", "test.skip('adds'", "describe.skip('x'"):
            after = self.B.replace("it('adds'", repl) if "describe" not in repl else self.B.replace("describe('x'", repl)
            self.assertEqual(blocks(self.js(after)), ["K2.skip"], repl)

    def test_conditional_skip_helpers_are_fine(self):
        self.assertEqual(blocks(self.js(self.B.replace("it('adds'", "it.skipIf(ci)('adds'"))), [])

    def test_empty_catch(self):
        after = self.B.replace("expect(sub(3, 1)).toBe(2);", "try { expect(sub(3, 1)).toBe(2); } catch (e) {}")
        found = self.js(after)
        self.assertEqual((blocks(found), notes(found)), ([], ["K4.swallow"]))

    def test_moved_between_files_is_not_removal(self):
        found = run(self.B, "", path="src/a.test.ts", extra=[("src/b.test.ts", None, self.B)])
        self.assertEqual(blocks(found), [])

    def test_non_test_js_untouched(self):
        self.assertEqual(run("const a = 1;\n", "const a = 2;\n", path="src/app.js"), [])


class Numbers(unittest.TestCase):
    def num(self, before, after, path="tests/test_x.py", cfg=None):
        return run(before, after, path=path, cfg=cfg)

    def test_tolerance_widened_in_tests(self):
        self.assertEqual(blocks(self.num("assert close(a, b, rel_tol=1e-9)\n", "assert close(a, b, rel_tol=0.5)\n")), ["K3.tol"])
        self.assertEqual(blocks(self.num("assert a == approx(b, abs=0.01)\n", "assert a == approx(b, abs=0.5)\n")), ["K3.tol"])
        self.assertEqual(blocks(self.num("self.assertAlmostEqual(a, b, places=7)\n", "self.assertAlmostEqual(a, b, places=2)\n")), ["K3.tol"])
        self.assertEqual(blocks(self.num("self.assertAlmostEqual(a, b, delta=0.1)\n", "self.assertAlmostEqual(a, b, delta=5)\n")), ["K3.tol"])
        self.assertEqual(blocks(self.num("expect(a).toBeCloseTo(b, 5);\n", "expect(a).toBeCloseTo(b, 2);\n", path="a.test.js")), ["K3.tol"])

    def test_tolerance_tightened_or_barely_moved(self):
        self.assertEqual(self.num("assert close(a, b, rel_tol=0.5)\n", "assert close(a, b, rel_tol=1e-9)\n"), [])
        found = self.num("assert close(a, b, rel_tol=1.0e-3)\n", "assert close(a, b, rel_tol=1.05e-3)\n")
        self.assertEqual((blocks(found), notes(found)), ([], ["K3.tol"]))

    def test_pass_lines_lowered(self):
        cases = [("MIN_PASS_SCORE = 90\n", "MIN_PASS_SCORE = 40\n", "gate.py"),
                 ("MIN_SCORE: int = 90\n", "MIN_SCORE: int = 40\n", "gate.py"),
                 ("[tool.coverage.report]\nfail_under = 90\n", "[tool.coverage.report]\nfail_under = 80\n", "pyproject.toml"),
                 ("addopts = --cov-fail-under=90\n", "addopts = --cov-fail-under=50\n", "pytest.ini"),
                 ('{"min_score": 0.8}\n', '{"min_score": 0.5}\n', "config.json"),
                 ("coverage_threshold: 85\n", "coverage_threshold: 60\n", "ci.yml"),
                 ('"coverageThreshold": {"global": {\n  "lines": 80\n}}\n', '"coverageThreshold": {"global": {\n  "lines": 50\n}}\n', "package.json")]
        for b, a, path in cases:
            self.assertEqual(blocks(self.num(b, a, path=path)), ["K5.lowered"], b)

    def test_limits_raised(self):
        self.assertEqual(blocks(self.num("max_failures = 0\n", "max_failures = 5\n", path="gate.py")), ["K5.raised"])
        self.assertEqual(blocks(self.num("max-complexity = 10\n", "max-complexity = 40\n", path="setup.cfg")), ["K5.raised"])

    def test_raising_a_pass_line_or_lowering_a_limit_is_fine(self):
        self.assertEqual(self.num("fail_under = 80\n", "fail_under = 90\n", path="pyproject.toml"), [])
        self.assertEqual(self.num("max_failures = 5\n", "max_failures = 0\n", path="gate.py"), [])

    def test_ambiguous_threshold_names_only_note(self):
        found = self.num("similarity_threshold = 0.8\n", "similarity_threshold = 0.6\n", path="match.py")
        self.assertEqual((blocks(found), notes(found)), ([], ["K5.lowered"]))

    def test_unrelated_numbers_are_ignored(self):
        for b, a in (("retries = 3\n", "retries = 10\n"), ("max_tokens = 800\n", "max_tokens = 1200\n"),
                     ("timeout = 5\n", "timeout = 60\n"), ("min_width = 10\n", "min_width = 2\n"),
                     ("port = 8080\n", "port = 9090\n")):
            self.assertEqual(self.num(b, a, path="app.py"), [], b)

    def test_pass_line_deleted(self):
        found = self.num("[tool.coverage.report]\nfail_under = 90\nshow_missing = true\n", "[tool.coverage.report]\nshow_missing = true\n", path="pyproject.toml")
        self.assertEqual(blocks(found), ["K5.deleted"])

    def test_an_ordinary_knob_that_disappears_is_only_a_note(self):
        found = self.num("MIN_SCORE = 0.5\nTOP_K = 5\n", "TOP_K = 5\n", path="skills.py")
        self.assertEqual((blocks(found), notes(found)), ([], ["K5.deleted"]))

    def test_pass_line_replaced_by_a_variable_is_not_judged(self):
        self.assertEqual(self.num("MIN_SCORE = 90\n", "MIN_SCORE = load_min()\n", path="gate.py"), [])

    def test_pinned_files_any_number_change_blocks(self):
        cfg = {**CFG, "pinned": ["ops/thresholds.json"]}
        b = '{"target": 100, "retries": 2}\n'
        self.assertEqual(blocks(self.num(b, '{"target": 40, "retries": 2}\n', path="ops/thresholds.json", cfg=cfg)), ["K5.pinned"])
        self.assertEqual(blocks(self.num(b, '{"target": 150, "retries": 2}\n', path="ops/thresholds.json", cfg=cfg)), ["K5.pinned"])
        self.assertEqual(self.num(b, '{"target": 100, "retries": 2}\n', path="ops/thresholds.json", cfg=cfg), [])
        self.assertEqual(blocks(self.num(b, None, path="ops/thresholds.json", cfg=cfg)), ["K5.pinned"])
        self.assertEqual(self.num(b, '{"target": 40, "retries": 2}\n', path="ops/other.json", cfg=cfg), [])


class Identity(unittest.TestCase):
    def test_ids_do_not_depend_on_line_numbers(self):
        a1 = BASE.replace('assert total == Decimal("19.99")', "assert total")
        a2 = "\n\n\n" + a1
        self.assertEqual({f.fp for f in run(BASE, a1)}, {f.fp for f in run("\n\n\n" + BASE, a2)})

    def test_glob_matching(self):
        self.assertTrue(bl.match_any("a/b/test_x.py", bl.DEFAULT_TEST_GLOBS))
        self.assertTrue(bl.match_any("tests/helpers/util.py", bl.DEFAULT_TEST_GLOBS))
        self.assertTrue(bl.match_any("web/x.spec.ts", bl.DEFAULT_TEST_GLOBS))
        self.assertFalse(bl.match_any("src/contest.py", bl.DEFAULT_TEST_GLOBS))


if __name__ == "__main__":
    unittest.main()
