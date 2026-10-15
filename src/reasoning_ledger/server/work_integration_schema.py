"""Persistent bounded reduction levels for reviewed parallel integration."""

SCHEMA = (
    """CREATE TABLE work_integration_state (
        controller_id TEXT NOT NULL REFERENCES work_controllers(id), round INTEGER NOT NULL,
        parent_task_id TEXT NOT NULL REFERENCES work_tasks(id),
        level INTEGER NOT NULL DEFAULT 0, cursor INTEGER NOT NULL DEFAULT 0,
        next_batch INTEGER NOT NULL DEFAULT 0, stage TEXT NOT NULL DEFAULT 'grouping'
        CHECK(stage IN ('grouping','waiting','ready')), fan_in INTEGER NOT NULL CHECK(fan_in>=2),
        input_count INTEGER NOT NULL DEFAULT 0, leaf_count INTEGER NOT NULL DEFAULT 0,
        PRIMARY KEY(controller_id,round)
    )""",
    """CREATE TABLE work_integration_inputs (
        controller_id TEXT NOT NULL REFERENCES work_controllers(id), round INTEGER NOT NULL, level INTEGER NOT NULL,
        ordinal INTEGER NOT NULL, reference_json TEXT NOT NULL,
        PRIMARY KEY(controller_id,round,level,ordinal)
    )""",
    """CREATE TABLE work_integration_groups (
        controller_id TEXT NOT NULL REFERENCES work_controllers(id), round INTEGER NOT NULL, level INTEGER NOT NULL,
        ordinal INTEGER NOT NULL, child_controller TEXT NOT NULL UNIQUE REFERENCES work_controllers(id),
        PRIMARY KEY(controller_id,round,level,ordinal)
    )""",
    "CREATE INDEX work_integration_children ON work_tasks(parent_id,id)",
    "CREATE INDEX work_integration_frozen_parent ON work_integration_state(parent_task_id,controller_id)",
    """CREATE TRIGGER work_integration_input_immutable BEFORE UPDATE ON work_integration_inputs
        BEGIN SELECT RAISE(ABORT,'integration inputs are immutable'); END""",
    """CREATE TRIGGER work_integration_input_retained BEFORE DELETE ON work_integration_inputs
        BEGIN SELECT RAISE(ABORT,'integration inputs are retained'); END""",
    """CREATE TRIGGER work_integration_group_immutable BEFORE UPDATE ON work_integration_groups
        BEGIN SELECT RAISE(ABORT,'integration groups are immutable'); END""",
    """CREATE TRIGGER work_integration_group_retained BEFORE DELETE ON work_integration_groups
        BEGIN SELECT RAISE(ABORT,'integration groups are retained'); END""",
)
