"""retire the provisional_vad turn start strategy

Revision ID: c4e21b7f80a9
Revises: f3a1c47b9e02
Create Date: 2026-09-13 17:10:00.000000

"""

import json
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c4e21b7f80a9"
down_revision: Union[str, None] = "f3a1c47b9e02"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_RETIRED_STRATEGY = "provisional_vad"
_REPLACEMENT_STRATEGY = "default"
_RETIRED_KEY = "provisional_vad_pause_secs"

_TARGETS = (
    ("workflows", "workflow_configurations"),
    ("workflow_definitions", "workflow_configurations"),
)


def _rewrite(config: dict) -> bool:
    """Rewrite one configuration dict in place. True if anything changed."""
    changed = False
    if config.get("turn_start_strategy") == _RETIRED_STRATEGY:
        config["turn_start_strategy"] = _REPLACEMENT_STRATEGY
        changed = True
    if _RETIRED_KEY in config:
        del config[_RETIRED_KEY]
        changed = True
    return changed


def upgrade() -> None:
    conn = op.get_bind()
    for table, column in _TARGETS:
        rows = conn.execute(
            sa.text(
                f"SELECT id, {column} FROM {table} "
                f"WHERE {column} IS NOT NULL "
                f"AND ({column}->>'turn_start_strategy' = :retired "
                f"     OR {column}->'{_RETIRED_KEY}' IS NOT NULL)"
            ),
            {"retired": _RETIRED_STRATEGY},
        ).fetchall()

        for row_id, raw in rows:
            config = json.loads(raw) if isinstance(raw, str) else raw
            if not isinstance(config, dict) or not _rewrite(config):
                continue
            conn.execute(
                sa.text(f"UPDATE {table} SET {column} = :cfg WHERE id = :id"),
                {"cfg": json.dumps(config), "id": row_id},
            )


def downgrade() -> None:
    pass
