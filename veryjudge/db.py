"""Postgres repository primitives over the eight migrated tables."""
from contextlib import contextmanager
import os

from sqlalchemy import MetaData, create_engine
from sqlalchemy.pool import NullPool


class Database:
    def __init__(self, url):
        options = {"pool_pre_ping": True}
        if os.environ.get("VERCEL"):
            options["poolclass"] = NullPool
        self.engine = create_engine(url, **options)
        self.metadata = MetaData()
        self.metadata.reflect(self.engine, only=["submissions", "propositions", "research_runs", "retrieval_steps",
                                                 "documents", "evidence", "judgments", "reviews"])
        self.tables = self.metadata.tables

    @contextmanager
    def transaction(self):
        with self.engine.begin() as connection:
            yield connection

    def insert(self, connection, table, values):
        return connection.execute(self.tables[table].insert().values(**values).returning(self.tables[table])).mappings().one()

    def get(self, connection, table, id, for_update=False):
        entity = self.tables[table]
        query = entity.select().where(entity.c.id == id)
        if for_update:
            query = query.with_for_update()
        return connection.execute(query).mappings().first()

    def rows(self, connection, table, **filters):
        entity = self.tables[table]
        return [dict(row) for row in connection.execute(entity.select().where(
            *(entity.c[key] == value for key, value in filters.items()))).mappings()]


def object_from_row(model, row):
    values = dict(row)
    for column, field in {"paragraphs_json": "paragraphs", "config_json": "config",
                          "results_json": "results", "machine_explanation_json": "machine_explanation",
                          "final_explanation_json": "final_explanation", "ancestry_edits_json": "ancestry_edits"}.items():
        if column in values:
            values[field] = values.pop(column)
    if "ancestry_role" in values:
        values["ancestry"] = dict(role=values.pop("ancestry_role"), root_document_id=values.pop("root_document_id"))
    if "locator_type" in values:
        values["locator"] = dict(type=values.pop("locator_type"), start=values.pop("locator_start"),
                                 end=values.pop("locator_end"), paragraph_index=values.pop("paragraph_index"))
    if values.get("paragraphs") is None and "paragraphs" in values:
        values["paragraphs"] = []
    return model.model_validate({key: value for key, value in values.items() if key in model.model_fields})


def row_from_object(obj, table):
    values = obj.model_dump(mode="python")
    for field, column in {"paragraphs": "paragraphs_json", "results": "results_json"}.items():
        if field in values:
            values[column] = values.pop(field)
    if "ancestry" in values:
        ancestry = values.pop("ancestry")
        values.update(ancestry_role=ancestry["role"], root_document_id=ancestry["root_document_id"])
    if "locator" in values:
        locator = values.pop("locator")
        values.update(locator_type=locator["type"], locator_start=locator["start"],
                      locator_end=locator["end"], paragraph_index=locator["paragraph_index"])
    return {key: value for key, value in values.items() if key in table.c and not (key == "created_at" and value is None)}
