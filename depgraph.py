#!/usr/bin/env python3
"""DepGraph compatibility entry point.

Running this file directly (``python3 depgraph.py ...``) works, and both the
installed ``depgraph`` console script and ``python3 -m depgraph`` dispatch to
the same implementation in the :mod:`depgraph` package. The package takes
import precedence over this shim for ``import depgraph``.
"""

from depgraph.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
