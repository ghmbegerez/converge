"""Tests for semantic processing: canonical text, embeddings, persistence, reindex (AR-10..AR-14)."""

import json

from conftest import make_intent

from converge import event_log
from converge.models import EventType, Intent, RiskLevel, Status
from converge.semantic.canonical import build_canonical_text, build_semantic_text, canonical_checksum
from converge.semantic.embeddings import (
    DeterministicProvider,
    EmbeddingProvider,
    get_provider,
)
from converge.semantic.indexer import index_intent, reindex


# ===================================================================
# AR-10: Canonical text builder
# ===================================================================

class TestCanonicalText:
    def test_deterministic_output(self):
        """Same intent produces same canonical text."""
        intent = Intent(
            id="c-001", source="feature/a", target="main",
            status=Status.READY, risk_level=RiskLevel.HIGH, priority=1,
            semantic={"problem_statement": "Fix auth", "objective": "Security"},
        )
        text1 = build_canonical_text(intent)
        text2 = build_canonical_text(intent)
        assert text1 == text2

    def test_checksum_deterministic(self):
        """Same text produces same checksum."""
        intent = Intent(
            id="c-002", source="feature/b", target="main",
            status=Status.READY,
            semantic={"objective": "Refactor"},
        )
        t = build_canonical_text(intent)
        assert canonical_checksum(t) == canonical_checksum(t)

    def test_different_intents_different_text(self):
        """Different intents produce different text."""
        i1 = Intent(id="c-003", source="feature/a", target="main", status=Status.READY)
        i2 = Intent(id="c-004", source="feature/b", target="main", status=Status.READY)
        assert build_canonical_text(i1) != build_canonical_text(i2)

    def test_semantic_keys_sorted(self):
        """Semantic dict keys are sorted for determinism."""
        intent = Intent(
            id="c-005", source="feature/x", target="main",
            status=Status.READY,
            semantic={"z_field": "last", "a_field": "first"},
        )
        text = build_canonical_text(intent)
        assert text.index("a_field") < text.index("z_field")

    def test_missing_fields_handled(self):
        """Empty semantic, no links, no coupling — still produces valid text."""
        intent = Intent(
            id="c-006", source="feature/x", target="main",
            status=Status.READY,
        )
        text = build_canonical_text(intent)
        assert "intent:c-006" in text
        assert "source:feature/x" in text

    def test_plan_id_included(self):
        """plan_id is included when present."""
        intent = Intent(
            id="c-007", source="feature/x", target="main",
            status=Status.READY, plan_id="plan-42",
        )
        text = build_canonical_text(intent)
        assert "plan:plan-42" in text

    def test_commit_links_included(self):
        """Commit links are included in canonical text."""
        intent = Intent(id="c-008", source="feature/x", target="main", status=Status.READY)
        links = [
            {"sha": "aaa111", "role": "head"},
            {"sha": "bbb222", "role": "base"},
        ]
        text = build_canonical_text(intent, commit_links=links)
        assert "link:aaa111:head" in text
        assert "link:bbb222:base" in text

    def test_coupling_included(self):
        """Coupling data is included in canonical text."""
        intent = Intent(id="c-009", source="feature/x", target="main", status=Status.READY)
        coupling = [{"file_a": "auth.py", "file_b": "db.py", "co_changes": 5}]
        text = build_canonical_text(intent, coupling=coupling)
        assert "coupling:auth.py:db.py:5" in text

    def test_dependencies_sorted(self):
        """Dependencies are sorted for determinism."""
        intent = Intent(
            id="c-010", source="feature/x", target="main",
            status=Status.READY, dependencies=["z-dep", "a-dep"],
        )
        text = build_canonical_text(intent)
        assert text.index("dep:a-dep") < text.index("dep:z-dep")

    def test_scope_hint_included(self):
        """Scope hints from technical metadata are included."""
        intent = Intent(
            id="c-011", source="feature/x", target="main",
            status=Status.READY,
            technical={"scope_hint": ["core", "api"]},
        )
        text = build_canonical_text(intent)
        assert "scope:api" in text
        assert "scope:core" in text


# ===================================================================
# Semantic text builder (excludes identity for embedding comparability)
# ===================================================================

class TestSemanticText:
    def test_semantic_text_excludes_id(self):
        """Semantic text must not contain intent ID."""
        intent = Intent(
            id="st-001", source="feature/a", target="main",
            status=Status.READY, risk_level=RiskLevel.HIGH,
            semantic={"objective": "Test"},
        )
        text = build_semantic_text(intent)
        assert "intent:" not in text
        assert "st-001" not in text

    def test_semantic_text_excludes_plan_id(self):
        """Semantic text must not contain plan ID."""
        intent = Intent(
            id="st-002", source="feature/a", target="main",
            status=Status.READY, plan_id="plan-99",
            semantic={"objective": "Test"},
        )
        text = build_semantic_text(intent)
        assert "plan:" not in text
        assert "plan-99" not in text

    def test_semantic_text_identical_for_same_content(self):
        """Two intents with different IDs but same semantic content produce identical text."""
        common = dict(
            source="feature/login", target="main",
            status=Status.READY, risk_level=RiskLevel.HIGH,
            semantic={"objective": "Add auth", "problem_statement": "Need login"},
            technical={"scope_hint": ["auth"]},
            dependencies=["dep-1"],
        )
        i1 = Intent(id="st-A", plan_id="plan-1", **common)
        i2 = Intent(id="st-B", plan_id="plan-2", **common)
        assert build_semantic_text(i1) == build_semantic_text(i2)

    def test_semantic_text_deterministic(self):
        """Same intent produces same semantic text on repeated calls."""
        intent = Intent(
            id="st-003", source="feature/x", target="main",
            status=Status.READY, semantic={"objective": "Repeat"},
        )
        assert build_semantic_text(intent) == build_semantic_text(intent)

    def test_semantic_text_includes_content_fields(self):
        """Semantic text includes source, target, risk, semantic metadata, scope."""
        intent = Intent(
            id="st-004", source="feature/x", target="main",
            status=Status.READY, risk_level=RiskLevel.MEDIUM,
            semantic={"objective": "Test"},
            technical={"scope_hint": ["core"]},
        )
        text = build_semantic_text(intent)
        assert "source:feature/x" in text
        assert "target:main" in text
        assert "risk:medium" in text
        assert "semantic.objective:Test" in text
        assert "scope:core" in text


# ===================================================================
# AR-11: Embedding provider
# ===================================================================

class TestEmbeddingProvider:
    def test_deterministic_provider_is_deterministic(self, db_path):
        """Same text produces same vector."""
        provider = DeterministicProvider(dimension=32)
        r1 = provider.embed("hello world")
        r2 = provider.embed("hello world")
        assert r1.vector == r2.vector

    def test_different_text_different_vector(self, db_path):
        """Different text produces different vector."""
        provider = DeterministicProvider(dimension=32)
        r1 = provider.embed("hello")
        r2 = provider.embed("world")
        assert r1.vector != r2.vector

    def test_vector_dimension(self, db_path):
        """Output dimension matches configured dimension."""
        for dim in (16, 64, 128):
            provider = DeterministicProvider(dimension=dim)
            r = provider.embed("test")
            assert len(r.vector) == dim
            assert r.dimension == dim

    def test_vector_normalized(self, db_path):
        """Output vectors are approximately L2-normalized."""
        provider = DeterministicProvider(dimension=64)
        r = provider.embed("test normalization")
        norm = sum(v * v for v in r.vector) ** 0.5
        assert abs(norm - 1.0) < 1e-6

    def test_model_name(self, db_path):
        """Provider reports its model name."""
        provider = DeterministicProvider()
        assert provider.model_name == "deterministic-v1"

    def test_batch_embed(self, db_path):
        """Batch embed returns results for all inputs."""
        provider = DeterministicProvider(dimension=16)
        results = provider.embed_batch(["a", "b", "c"])
        assert len(results) == 3
        assert all(len(r.vector) == 16 for r in results)

    def test_get_provider_factory(self, db_path):
        """get_provider returns a valid provider."""
        provider = get_provider("deterministic", dimension=32)
        assert isinstance(provider, EmbeddingProvider)
        assert provider.dimension == 32

    def test_get_provider_unknown_raises(self, db_path):
        """Unknown provider name raises ValueError."""
        try:
            get_provider("nonexistent")
            assert False, "Should have raised ValueError"
        except ValueError as e:
            assert "nonexistent" in str(e)


# ===================================================================
# AR-12: Embedding persistence
# ===================================================================

class TestEmbeddingPersistence:
    def test_upsert_and_get(self, db_path):
        """Persist and retrieve an embedding."""
        make_intent("emb-001")
        event_log.upsert_embedding(
        "emb-001", "test-model", 64,
            "checksum123", json.dumps([0.1] * 64),
        )
        emb = event_log.get_embedding("emb-001", "test-model")
        assert emb is not None
        assert emb["intent_id"] == "emb-001"
        assert emb["model"] == "test-model"
        assert emb["dimension"] == 64
        assert emb["checksum"] == "checksum123"

    def test_upsert_updates_existing(self, db_path):
        """Upserting same intent+model updates the record."""
        make_intent("emb-002")
        event_log.upsert_embedding(
        "emb-002", "test-model", 64,
            "old-checksum", json.dumps([0.1] * 64),
        )
        event_log.upsert_embedding(
        "emb-002", "test-model", 64,
            "new-checksum", json.dumps([0.2] * 64),
        )
        emb = event_log.get_embedding("emb-002", "test-model")
        assert emb["checksum"] == "new-checksum"

    def test_get_nonexistent(self, db_path):
        """Get non-existent embedding returns None."""
        assert event_log.get_embedding("nope", "nope") is None

    def test_delete_embedding(self, db_path):
        """Delete removes the embedding."""
        make_intent("emb-003")
        event_log.upsert_embedding(
        "emb-003", "test-model", 64,
            "checksum", json.dumps([0.1] * 64),
        )
        assert event_log.delete_embedding("emb-003", "test-model") is True
        assert event_log.get_embedding("emb-003", "test-model") is None

    def test_delete_nonexistent(self, db_path):
        """Deleting non-existent embedding returns False."""
        assert event_log.delete_embedding("nope", "nope") is False

    def test_list_embeddings(self, db_path):
        """List returns all embeddings."""
        make_intent("emb-004")
        make_intent("emb-005")
        event_log.upsert_embedding("emb-004", "m1", 64, "c1", "[]")
        event_log.upsert_embedding("emb-005", "m1", 64, "c2", "[]")
        embs = event_log.list_embeddings(model="m1")
        assert len(embs) == 2

    def test_embedding_coverage(self, db_path):
        """Coverage reports correct indexed/total."""
        make_intent("emb-006")
        make_intent("emb-007")
        make_intent("emb-008")
        # Index 2 of 3
        event_log.upsert_embedding("emb-006", "m1", 64, "c1", "[]")
        event_log.upsert_embedding("emb-007", "m1", 64, "c2", "[]")

        cov = event_log.embedding_coverage()
        assert cov["total_intents"] == 3
        assert cov["indexed"] == 2
        assert cov["not_indexed"] == 1
        assert cov["indexed_pct"] == round(2 / 3 * 100, 1)
        assert cov["last_model"] == "m1"


# ===================================================================
# AR-13: Reindex pipeline
# ===================================================================

class TestIndexer:
    def test_index_single_intent(self, db_path):
        """Index a single intent and verify persistence."""
        make_intent("idx-001", semantic={"objective": "Test"})
        provider = DeterministicProvider(dimension=32)
        result = index_intent("idx-001", provider)
        assert result["status"] == "indexed"
        assert result["model"] == "deterministic-v1"

        # Verify persisted
        emb = event_log.get_embedding("idx-001", "deterministic-v1")
        assert emb is not None
        assert emb["dimension"] == 32

    def test_index_skips_uptodate(self, db_path):
        """Skip indexing when checksum hasn't changed."""
        make_intent("idx-002", semantic={"objective": "Same"})
        provider = DeterministicProvider(dimension=32)
        r1 = index_intent("idx-002", provider)
        assert r1["status"] == "indexed"
        r2 = index_intent("idx-002", provider)
        assert r2["status"] == "skipped"

    def test_index_force_recomputes(self, db_path):
        """Force flag recomputes even when up-to-date."""
        make_intent("idx-003", semantic={"objective": "Force"})
        provider = DeterministicProvider(dimension=32)
        index_intent("idx-003", provider)
        r = index_intent("idx-003", provider, force=True)
        assert r["status"] == "indexed"

    def test_index_nonexistent_intent(self, db_path):
        """Indexing non-existent intent returns error."""
        provider = DeterministicProvider(dimension=32)
        r = index_intent("nope-999", provider)
        assert r["status"] == "error"

    def test_index_emits_event(self, db_path):
        """Indexing emits an embedding.generated event."""
        make_intent("idx-004")
        provider = DeterministicProvider(dimension=32)
        index_intent("idx-004", provider)
        events = event_log.query(event_type=EventType.EMBEDDING_GENERATED, intent_id="idx-004")
        assert len(events) >= 1

    def test_reindex_all(self, db_path):
        """Reindex all intents."""
        for i in range(5):
            make_intent(f"ri-{i:03d}", semantic={"n": str(i)})
        result = reindex(provider_name="deterministic")
        assert result["total"] == 5
        assert result["indexed"] == 5
        assert result["skipped"] == 0

    def test_reindex_skips_uptodate(self, db_path):
        """Second reindex skips already-indexed intents."""
        for i in range(3):
            make_intent(f"rs-{i:03d}")
        reindex(provider_name="deterministic")
        r2 = reindex(provider_name="deterministic")
        assert r2["indexed"] == 0
        assert r2["skipped"] == 3

    def test_reindex_dry_run(self, db_path):
        """Dry run reports what would happen without persisting."""
        for i in range(3):
            make_intent(f"rd-{i:03d}")
        result = reindex(provider_name="deterministic", dry_run=True)
        assert result["dry_run"] is True
        assert result["indexed"] == 3  # would be indexed
        # Nothing actually persisted
        embs = event_log.list_embeddings()
        assert len(embs) == 0

    def test_reindex_emits_event(self, db_path):
        """Reindex emits an embedding.reindexed event."""
        make_intent("re-001")
        reindex(provider_name="deterministic")
        events = event_log.query(event_type=EventType.EMBEDDING_REINDEXED)
        assert len(events) >= 1

    def test_reindex_tenant_filter(self, db_path):
        """Reindex respects tenant filter."""
        make_intent("rt-001", tenant_id="team-a")
        make_intent("rt-002", tenant_id="team-b")
        result = reindex(provider_name="deterministic", tenant_id="team-a")
        assert result["total"] == 1
        assert result["indexed"] == 1


# ===================================================================
# AR-14: Coverage API (tested via facade)
# ===================================================================

class TestCoverageAPI:
    def test_coverage_empty(self, db_path):
        """Coverage on empty DB returns zeros."""
        cov = event_log.embedding_coverage()
        assert cov["total_intents"] == 0
        assert cov["indexed"] == 0
        assert cov["indexed_pct"] == 0.0

    def test_coverage_with_tenant_filter(self, db_path):
        """Coverage filtered by tenant."""
        make_intent("cv-001", tenant_id="team-a")
        make_intent("cv-002", tenant_id="team-b")
        event_log.upsert_embedding("cv-001", "m1", 64, "c1", "[]")
        cov = event_log.embedding_coverage(tenant_id="team-a")
        assert cov["total_intents"] == 1
        assert cov["indexed"] == 1
        assert cov["indexed_pct"] == 100.0

    def test_coverage_with_model_filter(self, db_path):
        """Coverage filtered by model."""
        make_intent("cv-003")
        event_log.upsert_embedding("cv-003", "model-a", 64, "c1", "[]")
        event_log.upsert_embedding("cv-003", "model-b", 32, "c2", "[]")
        cov = event_log.embedding_coverage(model="model-a")
        assert cov["indexed"] == 1
