"""Authenticated persisted selection decides graph and selector versions."""

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..persistence.db import RunRoleOutputRecord
from .research_graph_recipe import read_graph_recipe_selection_in_session
from .research_graph_recipe_v3 import (
    SELECTION_SCHEMA_V3,
    FrozenGraphRecipeSelectionV3,
    read_graph_recipe_selection_v3_in_session,
)


def read_graph_recipe_selection_for_run_in_session(session: Session, run_id: str):
    schemas = list(
        session.scalars(
            select(RunRoleOutputRecord.schema_version).where(
                RunRoleOutputRecord.run_id == run_id,
                RunRoleOutputRecord.role == "graph_recipe_selection",
            )
        )
    )
    if len(schemas) > 1:
        raise ValueError("research_graph_recipe_selection_ambiguous")
    if schemas == [SELECTION_SCHEMA_V3]:
        return read_graph_recipe_selection_v3_in_session(session, run_id)
    return read_graph_recipe_selection_in_session(session, run_id)


def is_quality_graph_in_session(session: Session, run_id: str) -> bool:
    return isinstance(
        read_graph_recipe_selection_for_run_in_session(session, run_id),
        FrozenGraphRecipeSelectionV3,
    )
