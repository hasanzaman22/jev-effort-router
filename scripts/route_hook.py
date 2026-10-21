#!/usr/bin/env python3
"""Entry point for the UserPromptSubmit hook."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from jev_router.hook import main  # noqa: E402

sys.exit(main())
