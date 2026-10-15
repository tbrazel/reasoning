"""Personal model favorites and the initial beta-topic reasoning effort.

Copyright Benjamin Antieau and Deven Manam. SPDX-License-Identifier: Apache-2.0
"""

SCHEMA = (
    """CREATE TABLE user_model_favorites (
        user_id TEXT NOT NULL REFERENCES users(id), model_id TEXT NOT NULL,
        PRIMARY KEY(user_id,model_id)
    )""",
    "ALTER TABLE topics ADD COLUMN default_effort TEXT",
)
