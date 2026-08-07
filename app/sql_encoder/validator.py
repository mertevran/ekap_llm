"""SQLGlot AST üzerinden katı salt okunur SQL güvenlik doğrulaması."""

from __future__ import annotations

import hashlib
from collections.abc import Iterable

from sqlglot import exp, parse
from sqlglot.errors import ParseError

from app.sql_encoder.catalog import AllowedSchemaCatalog
from app.sql_encoder.models import SqlValidationReport


class UnsafeSqlError(ValueError):
    """SQL sorgusu izin verilen güvenlik sözleşmesini ihlal ettiğinde üretilir."""

    def __init__(self, errors: Iterable[str]) -> None:
        self.errors = tuple(dict.fromkeys(str(error) for error in errors if str(error)))
        super().__init__("; ".join(self.errors) or "SQL güvenlik doğrulaması başarısız.")


class SqlSafetyValidator:
    """Yalnız sınırlı SELECT sorgularını ve izinli EKAP alanlarını kabul eder."""

    _PROHIBITED_NODES = (
        exp.Insert,
        exp.Update,
        exp.Delete,
        exp.Drop,
        exp.Create,
        exp.Alter,
        exp.TruncateTable,
        exp.Merge,
        exp.Copy,
        exp.Command,
        exp.Transaction,
        exp.Lock,
        exp.Into,
        exp.With,
        exp.Union,
        exp.Intersect,
        exp.Except,
        exp.Lateral,
        exp.Values,
        exp.Window,
    )
    _ALLOWED_FUNCTIONS = frozenset(
        {
            "ABS",
            "AVG",
            "CAST",
            "COALESCE",
            "COUNT",
            "CURRENT_TIMESTAMP",
            "DATE",
            "EXISTS",
            "GREATEST",
            "LEAST",
            "LENGTH",
            "LOWER",
            "MAX",
            "MD5",
            "MIN",
            "NULLIF",
            "ROUND",
            "SUM",
            "UPPER",
        }
    )

    def __init__(
        self,
        *,
        catalog: AllowedSchemaCatalog,
        max_sql_chars: int,
        max_joins: int,
        max_ast_nodes: int,
    ) -> None:
        self.catalog = catalog
        self.max_sql_chars = max_sql_chars
        self.max_joins = max_joins
        self.max_ast_nodes = max_ast_nodes

    def validate(
        self,
        sql: str,
        *,
        parameter_count: int | None,
    ) -> SqlValidationReport:
        errors: list[str] = []
        normalized = str(sql or "").strip()
        query_hash = hashlib.sha256(normalized.encode("utf-8")).hexdigest()

        if not normalized:
            raise UnsafeSqlError(["SQL sorgusu boş olamaz."])
        if len(normalized) > self.max_sql_chars:
            raise UnsafeSqlError(
                [f"SQL uzunluğu {self.max_sql_chars} karakter sınırını aşıyor."]
            )
        if "--" in normalized or "/*" in normalized or "*/" in normalized:
            raise UnsafeSqlError(["SQL yorumları güvenlik nedeniyle kabul edilmez."])
        if parameter_count is not None and parameter_count < 0:
            raise UnsafeSqlError(["parameter_count negatif olamaz."])

        try:
            statements = [item for item in parse(normalized, read="postgres") if item]
        except ParseError as exc:
            raise UnsafeSqlError([f"SQL ayrıştırılamadı: {exc}"]) from exc

        if len(statements) != 1:
            raise UnsafeSqlError(["Tam olarak bir SQL ifadesine izin verilir."])

        statement = statements[0]
        if not isinstance(statement, exp.Select):
            errors.append("Yalnızca SELECT sorgularına izin verilir.")

        nodes = list(statement.walk())
        if len(nodes) > self.max_ast_nodes:
            errors.append(
                f"SQL yapısı {self.max_ast_nodes} düğümlük karmaşıklık sınırını aşıyor."
            )

        for prohibited_type in self._PROHIBITED_NODES:
            if any(isinstance(node, prohibited_type) for node in nodes):
                errors.append(
                    f"Yasak SQL yapısı bulundu: {prohibited_type.__name__}."
                )

        tables = list(statement.find_all(exp.Table))
        if not tables:
            errors.append("SELECT sorgusu en az bir izinli tablo kullanmalıdır.")

        alias_to_table: dict[str, str] = {}
        qualified_tables: list[str] = []
        referenced_tables: set[str] = set()
        for table in tables:
            schema = str(table.db or self.catalog.schema_name).casefold()
            table_name = str(table.name or "").casefold()
            if table.catalog:
                errors.append("Veritabanı kataloğu belirten tablo adlarına izin verilmez.")
                continue
            if not self.catalog.is_allowed_table(schema=schema, table=table_name):
                errors.append(f"İzin verilmeyen tablo: {schema}.{table_name or '?'}.")
                continue
            referenced_tables.add(table_name)
            qualified_tables.append(f"{schema}.{table_name}")
            alias = str(table.alias_or_name or table_name).casefold()
            alias_to_table[alias] = table_name
            alias_to_table[table_name] = table_name

        joins = list(statement.find_all(exp.Join))
        if len(joins) > self.max_joins:
            errors.append(f"En fazla {self.max_joins} JOIN kullanılabilir.")
        for join in joins:
            if join.args.get("on") is None:
                errors.append("CROSS veya ON koşulu olmayan JOIN kullanımına izin verilmez.")

        projection_aliases = {
            str(item.alias).casefold()
            for select in statement.find_all(exp.Select)
            for item in select.expressions
            if item.alias
        }
        referenced_columns = {
            column
            for table_name in referenced_tables
            for column in self.catalog.allowed_tables[table_name]
        }
        column_names: list[str] = []
        for column in statement.find_all(exp.Column):
            column_name = str(column.name or "").casefold()
            qualifier = str(column.table or "").casefold()
            column_names.append(
                f"{qualifier}.{column_name}" if qualifier else column_name
            )
            if column_name in projection_aliases and not qualifier:
                continue
            if qualifier:
                resolved_table = alias_to_table.get(qualifier)
                if resolved_table is None:
                    errors.append(f"Bilinmeyen tablo takma adı: {qualifier}.")
                elif not self.catalog.is_allowed_column(
                    table=resolved_table,
                    column=column_name,
                ):
                    errors.append(
                        f"İzin verilmeyen sütun: {qualifier}.{column_name or '?'}."
                    )
            elif column_name not in referenced_columns:
                errors.append(f"İzin verilmeyen sütun: {column_name or '?'}.")

        for star in statement.find_all(exp.Star):
            if not isinstance(star.parent, exp.Count):
                errors.append(
                    "SELECT * kullanımına izin verilmez; sütunlar açıkça belirtilmelidir."
                )

        function_names: list[str] = []
        for function in statement.find_all(exp.Func):
            if isinstance(function, exp.Connector):
                continue
            if isinstance(function, exp.Anonymous):
                function_name = str(function.name or "").upper()
            else:
                function_name = str(function.sql_name() or "").upper()
            function_names.append(function_name)
            if function_name not in self._ALLOWED_FUNCTIONS:
                errors.append(f"İzin verilmeyen SQL işlevi: {function_name or '?'}.")

        placeholder_count = sum(
            1 for node in nodes if isinstance(node, exp.Placeholder)
        )
        if parameter_count is not None and placeholder_count != parameter_count:
            errors.append(
                "SQL yer tutucu sayısı ile parametre sayısı uyuşmuyor: "
                f"yer_tutucu={placeholder_count}, parametre={parameter_count}."
            )

        if errors:
            raise UnsafeSqlError(errors)

        return SqlValidationReport(
            valid=True,
            query_hash=query_hash,
            statement_type="SELECT",
            tables=sorted(set(qualified_tables)),
            columns=sorted(set(column_names)),
            functions=sorted(set(function_names)),
            placeholder_count=placeholder_count,
            join_count=len(joins),
            ast_node_count=len(nodes),
        )


__all__ = ["SqlSafetyValidator", "UnsafeSqlError"]
