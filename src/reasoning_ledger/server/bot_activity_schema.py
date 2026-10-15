"""Bounded bot activity summaries.

Copyright Benjamin Antieau and Deven Manam. SPDX-License-Identifier: Apache-2.0
"""

SCHEMA = ("ALTER TABLE bot_runs ADD COLUMN activity_json TEXT NOT NULL DEFAULT '[]'",)
