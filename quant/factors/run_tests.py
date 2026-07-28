"""无 pytest 也能跑：python -m quant.factors.run_tests2"""
from __future__ import annotations
import traceback
import importlib


def main() -> None:
    mod = importlib.import_module("quant.factors.test_factors")
    passed = 0
    failed = 0
    for name in sorted(dir(mod)):
        if not name.startswith("test_"):
            continue
        fn = getattr(mod, name)
        if not callable(fn):
            continue
        try:
            fn()
            print(f"PASS {name}")
            passed += 1
        except Exception:
            print(f"FAIL {name}")
            traceback.print_exc()
            failed += 1
    print(f"\n{passed} passed, {failed} failed")
    raise SystemExit(1 if failed else 0)


if __name__ == "__main__":
    main()
