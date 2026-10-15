"""Print the exact ORM index and constraint inventory without connecting to PostgreSQL."""

from sqlalchemy import CheckConstraint, ForeignKeyConstraint, PrimaryKeyConstraint, UniqueConstraint
from sqlalchemy.dialects import postgresql
from sqlalchemy.schema import CreateIndex

from .models import Base


def main() -> None:
    dialect = postgresql.dialect()
    for table_name, table in sorted(Base.metadata.tables.items()):
        print(f"\n## {table_name}")
        for constraint in sorted(table.constraints, key=lambda item: str(item.name)):
            if isinstance(constraint, PrimaryKeyConstraint):
                detail = "PRIMARY KEY " + ", ".join(column.name for column in constraint.columns)
            elif isinstance(constraint, UniqueConstraint):
                detail = "UNIQUE " + ", ".join(column.name for column in constraint.columns)
            elif isinstance(constraint, ForeignKeyConstraint):
                detail = "FOREIGN KEY " + ", ".join(f"{edge.parent.name} -> {edge.target_fullname}" for edge in constraint.elements)
                detail += " ON DELETE RESTRICT DEFERRABLE INITIALLY DEFERRED"
            elif isinstance(constraint, CheckConstraint):
                detail = "CHECK " + str(constraint.sqltext)
            else:
                raise TypeError(f"Unrecognized constraint: {type(constraint).__name__}")
            physical_name = dialect.identifier_preparer.format_constraint(constraint)
            print(f"- {physical_name}: {detail}")
        for index in sorted(table.indexes, key=lambda item: str(item.name)):
            print(f"- {CreateIndex(index).compile(dialect=dialect)}")


if __name__ == "__main__":
    main()