#!/usr/bin/env python3
"""Entry point for the /jev-effort-router:enable, :disable, and :status slash commands."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from jev_router.command import main  # noqa: E402

sys.exit(main())
