#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""
patch_self_reflection_project_memory_v1.py

Adds ProjectMemory responsibility to tools/self_reflection.py.

Dry-run by default. Use --apply to write.

Usage:

  cd G:\Programming\Py\OSBB

  python .\tools\patch_self_reflection_project_memory_v1.py ^
    --self-reflection "G:\Programming\Py\OSBB\tools\self_reflection.py"

  python .\tools\patch_self_reflection_project_memory_v1.py ^
    --self-reflection "G:\Programming\Py\OSBB\tools\self_reflection.py" ^
    --apply
"""

from __future__ import annotations

import argparse
import shutil
from datetime import datetime
from pathlib import Path


MARKER = "OSBB_PROJECT_MEMORY_V1"


MANIFEST = r"""
# ==============================================================================
#
#                           PROJECT MEMORY MANIFEST
#                           OSBB_PROJECT_MEMORY_V1
#
# ==============================================================================
#
# Date:
#     2026-06-30 / fixed in code on 2026-07-01
#
# Decision:
#     The OSBB project introduces ProjectMemory as its engineering memory.
#
# ProjectMemory stores not only what was built, but why it was built:
# architectural decisions, release recommendations, reasons for promoting
# one database branch and rejecting another, risks, lessons, and unfinished ideas.
#
# ProjectMemory structure:
#
#     ProjectMemory/
#     ├── README.md
#     ├── 2026/
#     │   ├── README.md
#     │   ├── 2026-06-30_H1_Finale.md
#     │   ├── 2026-07-01_Decisions.md
#     │   ├── Architecture.md
#     │   ├── Vision.md
#     │   ├── Roadmap.md
#     │   ├── Dreams.md
#     │   ├── LessonsLearned.md
#     │   └── ReleaseHistory.md
#     ├── Decisions/
#     ├── Releases/
#     ├── Reflections/
#     ├── Architecture/
#     ├── Archaeology/
#     └── Reports/
#
# Responsibility:
#     Beginning with 2026-06-30, this module, self_reflection.py, is declared
#     the official keeper of ProjectMemory.
#
# Motto:
#     A mature project remembers not only WHAT was built, but also WHY it was built.
#
# ==============================================================================
""".strip()


HELPERS = r