#!/usr/bin/env python3
"""Entry point for the jev-effort-router mod: picks the effort the session switches to."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from jev_router.hook import main_switch  # noqa: E402

sys.exit(main_switch())
