from __future__ import annotations

import argparse
import sys
import unittest


def main() -> int:
    parser = argparse.ArgumentParser(description="Run all unit tests")
    parser.add_argument("-q", "--quiet", action="store_true", help="Reduce test output")
    args = parser.parse_args()

    loader = unittest.TestLoader()
    suite = loader.discover(start_dir="tests", pattern="test_*.py")
    verbosity = 1 if args.quiet else 2
    result = unittest.TextTestRunner(verbosity=verbosity).run(suite)
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    raise SystemExit(main())
