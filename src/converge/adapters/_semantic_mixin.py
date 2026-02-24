"""Semantic store mixins: embeddings and chain state.

These mixin classes provide the business methods for EmbeddingStorePort
and ChainStatePort.  They rely on ``_StoreDialect`` methods being available
via MRO.
"""

from __future__ import annotations

from typing import Any

from converge.models import now_iso


# ---------------------------------------------------------------------------
# EmbeddingStoreMixin
# ---------------------------------------------------------------------------

class EmbeddingStoreMixin:
    """Mixin providing EmbeddingStorePort methods."""

    def upsert_embedding(
        self, intent_id: str, model: str, dimension: int,
        checksum: str, vector: str, generated_at: str,
    ) -> None:
        ph = self._ph
        ex = self._excluded_prefix
        with self._connection() as conn:
            conn.execute(
                f"INSERT INTO intent_embeddings "
                f"(intent_id, model, dimension, checksum, vector, generated_at) "
                f"VALUES ({self._placeholders(6)}) "
                f"ON CONFLICT(intent_id, model) DO UPDATE SET "
                f"dimension={ex}.dimension, checksum={ex}.checksum, "
                f"vector={ex}.vector, generated_at={ex}.generated_at",
                (intent_id, model, dimension, checksum, vector, generated_at),
            )
            conn.commit()

    def get_embedding(
        self, intent_id: str, model: str,
    ) -> dict[str, Any] | None:
        ph = self._ph
        with self._connection() as conn:
            row = conn.execute(
                f"SELECT * FROM intent_embeddings "
                f"WHERE intent_id = {ph} AND model = {ph}",
                (intent_id, model),
            ).fetchone()
        return dict(row) if row else None

    def list_embeddings(
        self, *, tenant_id: str | None = None, model: str | None = None,
        limit: int = 1000,
    ) -> list[dict[str, Any]]:
        ph = self._ph
        clauses: list[str] = []
        params: list[Any] = []
        if tenant_id:
            clauses.append(
                f"intent_id IN (SELECT id FROM intents WHERE tenant_id = {ph})"
            )
            params.append(tenant_id)
        if model:
            clauses.append(f"model = {ph}")
            params.append(model)
        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
        params.append(limit)
        with self._connection() as conn:
            rows = conn.execute(
                f"SELECT * FROM intent_embeddings{where} "
                f"ORDER BY generated_at DESC LIMIT {ph}",
                params,
            ).fetchall()
        return [dict(r) for r in rows]

    def delete_embedding(self, intent_id: str, model: str) -> bool:
        ph = self._ph
        with self._connection() as conn:
            cur = conn.execute(
                f"DELETE FROM intent_embeddings "
                f"WHERE intent_id = {ph} AND model = {ph}",
                (intent_id, model),
            )
            conn.commit()
        return cur.rowcount > 0

    def embedding_coverage(
        self, *, tenant_id: str | None = None, model: str | None = None,
    ) -> dict[str, Any]:
        """Return indexed/stale/total counts for embedding coverage."""
        ph = self._ph
        with self._connection() as conn:
            # Total intents
            if tenant_id:
                total = conn.execute(
                    f"SELECT COUNT(*) AS cnt FROM intents WHERE tenant_id = {ph}",
                    (tenant_id,),
                ).fetchone()["cnt"]
            else:
                total = conn.execute(
                    "SELECT COUNT(*) AS cnt FROM intents",
                ).fetchone()["cnt"]

            # Indexed: intents that have an embedding matching current checksum
            # (stale = has embedding but checksum changed; we track indexed only)
            emb_clauses: list[str] = []
            emb_params: list[Any] = []
            if tenant_id:
                emb_clauses.append(
                    f"e.intent_id IN (SELECT id FROM intents WHERE tenant_id = {ph})"
                )
                emb_params.append(tenant_id)
            if model:
                emb_clauses.append(f"e.model = {ph}")
                emb_params.append(model)
            emb_where = (" WHERE " + " AND ".join(emb_clauses)) if emb_clauses else ""

            indexed = conn.execute(
                f"SELECT COUNT(DISTINCT e.intent_id) AS cnt "
                f"FROM intent_embeddings e{emb_where}",
                emb_params,
            ).fetchone()["cnt"]

            # Last model/version used
            last_row = conn.execute(
                f"SELECT model, generated_at FROM intent_embeddings "
                f"ORDER BY generated_at DESC LIMIT 1",
            ).fetchone()

        not_indexed = total - indexed
        return {
            "total_intents": total,
            "indexed": indexed,
            "not_indexed": not_indexed,
            "indexed_pct": round(indexed / total * 100, 1) if total else 0.0,
            "last_model": last_row["model"] if last_row else None,
            "last_generated_at": last_row["generated_at"] if last_row else None,
        }


# ---------------------------------------------------------------------------
# ChainStateMixin
# ---------------------------------------------------------------------------

class ChainStateMixin:
    """Mixin providing ChainStatePort methods."""

    def get_chain_state(self, chain_id: str = "main") -> dict[str, Any] | None:
        ph = self._ph
        with self._connection() as conn:
            row = conn.execute(
                f"SELECT * FROM event_chain_state WHERE chain_id = {ph}",
                (chain_id,),
            ).fetchone()
        return dict(row) if row else None

    def save_chain_state(self, chain_id: str, last_hash: str, event_count: int) -> None:
        ph = self._ph
        ex = self._excluded_prefix
        with self._connection() as conn:
            conn.execute(
                f"""INSERT INTO event_chain_state (chain_id, last_hash, event_count, updated_at)
                VALUES ({ph}, {ph}, {ph}, {ph})
                ON CONFLICT(chain_id) DO UPDATE SET
                    last_hash={ex}.last_hash, event_count={ex}.event_count,
                    updated_at={ex}.updated_at""",
                (chain_id, last_hash, event_count, now_iso()),
            )
            conn.commit()
