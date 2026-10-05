"""**Progressive Entity Enrichment** — the core resolution engine.

Algorithm: iterative BFS over the identifier graph
--------------------------------------------------
Nodes  : ``entity_identifiers`` rows, keyed by ``(canonical_type, normalized_value)``
Edges  : two source records are adjacent when they share at least one
         ``(canonical_type, normalized_value)`` pair of a *linkable* type.

Given a seed query (e.g. ``john@example.com``):

    frontier := {normalized identifiers derived from the query}
    hop := 1
    loop:
        1. SELECT source records that carry any identifier in `frontier`
        2. drop records already visited; drop identifiers already searched
        3. for each new record, extract its identifiers -> `next frontier`
        4. record the hop in the timeline (which identifier pulled in which rows)
        5. frontier := next frontier - searched
        stop when frontier is empty, no new records appear, or hop > MAX_BFS_DEPTH

Every hop is persisted into ``entity_records`` with ``discovered_at_hop`` and
``matched_on_*`` so the UI can render an exact, auditable discovery timeline and
the consolidated entity can always be traced back to its source rows.

The same traversal doubles as the **indexer**: after ingestion we run a
global, identifier-driven union-find clustering pass so that every record
already carries a ``master_entity_id`` and the repository is queryable without
running a search first.
"""
from __future__ import annotations

import logging
import time
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any, Iterable, Sequence

from sqlalchemy import and_, delete, exists, func, or_, select, text, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.db.models import (
    Dataset,
    EntityIdentifier,
    EntityRecord,
    MasterEntity,
    SourceRecord,
)
from app.services.normalize import (
    IDENTIFIABLE_TYPES,
    normalize_field,
    normalize_phone,
)

logger = logging.getLogger(__name__)

# --------------------------------------------------------------------------- #
# DSU
# --------------------------------------------------------------------------- #
class UnionFind:
    """Disjoint-set forest with union-by-size and path halving."""

    __slots__ = ("parent", "rank")

    def __init__(self) -> None:
        self.parent: dict[int, int] = {}
        self.rank: dict[int, int] = {}

    def add(self, x: int) -> None:
        if x not in self.parent:
            self.parent[x] = x
            self.rank[x] = 0

    def find(self, x: int) -> int:
        self.add(x)
        root = x
        while self.parent[root] != root:
            root = self.parent[root]
        while self.parent[x] != root:
            self.parent[x], x = root, self.parent[x]
        return root

    def union(self, a: int, b: int) -> int:
        ra, rb = self.find(a), self.find(b)
        if ra == rb:
            return ra
        if self.rank[ra] < self.rank[rb]:
            ra, rb = rb, ra
        self.parent[rb] = ra
        if self.rank[ra] == self.rank[rb]:
            self.rank[ra] += 1
        return ra

    def groups(self) -> dict[int, list[int]]:
        out: dict[int, list[int]] = defaultdict(list)
        for node in self.parent:
            out[self.find(node)].append(node)
        return out


# --------------------------------------------------------------------------- #
# seed query interpretation
# --------------------------------------------------------------------------- #
def infer_seed_type(query: str) -> tuple[str, str | None]:
    """Return ``(canonical_type, normalized_value)`` for a free-text query."""
    q = (query or "").strip()
    if not q:
        return "unknown", None
    if "@" in q:
        res = normalize_field("email", q)
        if res.normalized:
            return "email", res.normalized
    digits = "".join(c for c in q if c.isdigit())
    if len(digits) >= 7 and len(digits) <= 15:
        res = normalize_phone(q)
        if res.normalized:
            return "phone", res.normalized
    res = normalize_field("email", q)
    if res.normalized:
        return "email", res.normalized
    res = normalize_field("member_id", q)
    if res.normalized:
        return "member_id", res.normalized
    res = normalize_field("username", q)
    if res.normalized:
        return "username", res.normalized
    res = normalize_field("name", q)
    if res.normalized:
        return "name", res.normalized
    return "unknown", None


# --------------------------------------------------------------------------- #
# results
# --------------------------------------------------------------------------- #
@dataclass(slots=True)
class Hop:
    hop: int
    seed_identifiers: list[dict[str, Any]]
    matched_records: int
    new_records: int
    new_identifiers: list[dict[str, Any]]
    datasets_touched: list[str]
    sources_touched: list[str]
    duration_ms: int = 0

    def as_dict(self) -> dict[str, Any]:
        return {
            "hop": self.hop,
            "seed_identifiers": self.seed_identifiers,
            "matched_records": self.matched_records,
            "new_records": self.new_records,
            "new_identifiers": self.new_identifiers,
            "datasets_touched": self.datasets_touched,
            "sources_touched": self.sources_touched,
            "duration_ms": self.duration_ms,
        }


@dataclass(slots=True)
class ResolutionResult:
    master_entity_id: int | None
    seed_type: str
    seed_value: str | None
    hops: list[Hop] = field(default_factory=list)
    record_ids: list[int] = field(default_factory=list)
    identifiers: list[dict[str, Any]] = field(default_factory=list)
    entities_touched: list[int] = field(default_factory=list)
    duration_ms: int = 0
    exhausted: bool = True
    truncated: bool = False

    @property
    def hop_count(self) -> int:
        return len(self.hops)

    def as_dict(self) -> dict[str, Any]:
        return {
            "master_entity_id": self.master_entity_id,
            "seed_type": self.seed_type,
            "seed_value": self.seed_value,
            "hops": [h.as_dict() for h in self.hops],
            "hop_count": self.hop_count,
            "record_count": len(self.record_ids),
            "identifiers": self.identifiers,
            "entities_touched": self.entities_touched,
            "duration_ms": self.duration_ms,
            "exhausted": self.exhausted,
            "truncated": self.truncated,
        }


# --------------------------------------------------------------------------- #
# the BFS
# --------------------------------------------------------------------------- #
class ProgressiveResolver:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.linkable = tuple(settings.LINKABLE_TYPES)

    async def resolve(
        self,
        query: str,
        *,
        max_depth: int | None = None,
        include_weak: bool = True,
    ) -> ResolutionResult:
        t0 = time.perf_counter()
        max_depth = max_depth or settings.MAX_BFS_DEPTH
        seed_type, seed_value = infer_seed_type(query)

        result = ResolutionResult(
            master_entity_id=None, seed_type=seed_type, seed_value=seed_value
        )
        if not seed_value:
            return result

        # how strongly should we trust each identifier type when it is used to
        # pull a record in? name-only links are reported but never merge on
        # their own.
        weights = dict(settings.IDENTIFIER_WEIGHTS)
        linkable = self.linkable if include_weak else tuple(
            t for t in self.linkable if t != "name"
        )

        frontier: set[tuple[str, str]] = {(seed_type, seed_value)}
        searched: set[tuple[str, str]] = set()
        visited_records: dict[int, int] = {}   # record_id -> hop discovered
        all_identifiers: dict[tuple[str, str], dict[str, Any]] = {}
        hop_no = 0

        while frontier and hop_no < max_depth:
            hop_t0 = time.perf_counter()
            hop_no += 1
            hop = Hop(
                hop=hop_no,
                seed_identifiers=[
                    {"canonical_type": t, "normalized_value": v,
                     "confidence": weights.get(t, 0.5)}
                    for (t, v) in sorted(frontier)
                ],
                matched_records=0,
                new_records=0,
                new_identifiers=[],
                datasets_touched=[],
                sources_touched=[],
            )

            batch = list(frontier)[: settings.BFS_BATCH_SIZE]
            if len(frontier) > settings.BFS_BATCH_SIZE:
                result.truncated = True

            record_ids = await self._records_for_identifiers(batch)
            hop.matched_records = len(record_ids)

            new_ids = [r for r in record_ids if r not in visited_records]
            # name-only matches are surfaced in the timeline but do not become
            # part of the consolidated entity unless corroborated.
            strong_new: list[int] = []
            if new_ids:
                matched_map = await self._match_reason(new_ids, batch)
                for rid in new_ids:
                    reason = matched_map.get(rid, {})
                    if reason.get("canonical_type") in linkable or seed_type == reason.get("canonical_type"):
                        strong_new.append(rid)
                    elif reason.get("canonical_type") == "name" and include_weak:
                        visited_records[rid] = hop_no
                        hop.new_identifiers.append(
                            {
                                "canonical_type": reason.get("canonical_type"),
                                "normalized_value": reason.get("normalized_value"),
                                "weak": True,
                                "note": "name-only similarity, not used to merge",
                            }
                        )

            hop.new_records = len(strong_new)
            for rid in strong_new:
                visited_records[rid] = hop_no

            if strong_new:
                idents = await self._identifiers_for_records(strong_new)
                next_frontier: set[tuple[str, str]] = set()
                ds_ids: set[int] = set()
                for item in idents:
                    key = (item["canonical_type"], item["normalized_value"])
                    all_identifiers.setdefault(key, item)
                    if item["canonical_type"] in linkable and key not in searched:
                        next_frontier.add(key)
                    if item.get("dataset_id"):
                        ds_ids.add(item["dataset_id"])
                frontier = {k for k in next_frontier if k not in searched}
                if ds_ids:
                    names = await self._dataset_names(ds_ids)
                    hop.datasets_touched = sorted(names.values())
                    hop.sources_touched = sorted({v for v in names.values()})
            else:
                frontier = set()

            searched.update(batch)
            hop.duration_ms = int((time.perf_counter() - hop_t0) * 1000)
            result.hops.append(hop)

            if not strong_new and not frontier:
                break

        result.record_ids = list(visited_records)
        result.identifiers = [all_identifiers[k] for k in sorted(all_identifiers)]
        result.duration_ms = int((time.perf_counter() - t0) * 1000)
        result.exhausted = not frontier

        if result.record_ids:
            result.entities_touched = await self._entities_for_records(result.record_ids)
            result.master_entity_id = result.entities_touched[0] if result.entities_touched else None

        return result

    async def _records_for_identifiers(
        self, pairs: Sequence[tuple[str, str]]
    ) -> list[int]:
        if not pairs:
            return []
        clauses = [
            and_(EntityIdentifier.canonical_type == t, EntityIdentifier.normalized_value == v)
            for t, v in pairs
        ]
        stmt = select(EntityIdentifier.source_record_id).where(or_(*clauses))
        rows = await self.session.execute(stmt)
        return list({r[0] for r in rows.all()})

    async def _match_reason(
        self, record_ids: Sequence[int], pairs: Sequence[tuple[str, str]]
    ) -> dict[int, dict[str, Any]]:
        """Which frontier identifier explains each record's arrival?"""
        if not record_ids or not pairs:
            return {}
        clauses = [
            and_(EntityIdentifier.canonical_type == t, EntityIdentifier.normalized_value == v)
            for t, v in pairs
        ]
        stmt = (
            select(
                EntityIdentifier.source_record_id,
                EntityIdentifier.canonical_type,
                EntityIdentifier.normalized_value,
            )
            .where(and_(EntityIdentifier.source_record_id.in_(list(record_ids)), or_(*clauses)))
        )
        rows = (await self.session.execute(stmt)).all()
        out: dict[int, dict[str, Any]] = {}
        weights = settings.IDENTIFIER_WEIGHTS
        best: dict[int, float] = {}
        for rid, ctype, value in rows:
            score = weights.get(ctype, 0.4)
            if score >= best.get(rid, -1.0):
                best[rid] = score
                out[rid] = {"canonical_type": ctype, "normalized_value": value, "score": score}
        return out

    async def _identifiers_for_records(self, record_ids: Sequence[int]) -> list[dict[str, Any]]:
        if not record_ids:
            return []
        stmt = select(
            EntityIdentifier.canonical_type,
            EntityIdentifier.normalized_value,
            EntityIdentifier.raw_value,
            EntityIdentifier.confidence,
            EntityIdentifier.is_primary,
            EntityIdentifier.source_record_id,
            EntityIdentifier.source_id,
            EntityIdentifier.dataset_id,
        ).where(EntityIdentifier.source_record_id.in_(list(record_ids)))
        rows = (await self.session.execute(stmt)).all()
        return [
            {
                "canonical_type": r[0], "normalized_value": r[1], "raw_value": r[2],
                "confidence": r[3], "is_primary": r[4], "source_record_id": r[5],
                "source_id": r[6], "dataset_id": r[7],
            }
            for r in rows
        ]

    async def _dataset_names(self, ids: Iterable[int]) -> dict[int, str]:
        ids = list(ids)
        if not ids:
            return {}
        rows = await self.session.execute(
            select(Dataset.id, Dataset.name).where(Dataset.id.in_(ids))
        )
        return {r[0]: r[1] for r in rows.all()}

    async def _entities_for_records(self, record_ids: Sequence[int]) -> list[int]:
        if not record_ids:
            return []
        rows = await self.session.execute(
            select(EntityIdentifier.master_entity_id)
            .where(
                and_(
                    EntityIdentifier.source_record_id.in_(list(record_ids)),
                    EntityIdentifier.master_entity_id.isnot(None),
                )
            )
            .distinct()
        )
        ids = [r[0] for r in rows.all() if r[0]]
        if len(ids) <= 1:
            return ids
        # prefer the entity covering the most of the found records
        counts = await self.session.execute(
            select(EntityRecord.master_entity_id, func.count(EntityRecord.id))
            .where(EntityRecord.master_entity_id.in_(ids))
            .group_by(EntityRecord.master_entity_id)
            .order_by(func.count(EntityRecord.id).desc())
        )
        out: list[int] = []
        for eid, _c in counts.all():
            out.append(eid)
        return out


# --------------------------------------------------------------------------- #
# global clustering (the indexer)
# --------------------------------------------------------------------------- #
class ClusterIndexer:
    """Build/refresh master entities for every record in the repository.

    Runs after each import. Uses an in-memory union-find over the identifier
    index (a single pass over distinct ``(type, value)`` keys), then reconciles
    the resulting clusters with ``master_entities``:

      * a cluster that contains an existing entity  -> that entity is reused
        (its counters/attributes are refreshed)
      * a cluster with no entity                   -> a new entity is created
      * an entity that ends up with no members     -> deleted (stale)

    This is what keeps multi-part imports from creating duplicate entities: the
    second upload of the same logical rows maps onto the same identifier keys
    and therefore the same cluster.
    """

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def run(
        self,
        *,
        dataset_ids: Sequence[int] | None = None,
        only_new: bool = False,
    ) -> dict[str, int]:
        t0 = time.perf_counter()
        scope = (
            EntityIdentifier.dataset_id.in_(list(dataset_ids))
            if dataset_ids
            else None
        )

        # ---- 1. load the identifier index into memory -------------------
        stmt = select(
            EntityIdentifier.id,
            EntityIdentifier.canonical_type,
            EntityIdentifier.normalized_value,
            EntityIdentifier.source_record_id,
            EntityIdentifier.source_id,
            EntityIdentifier.dataset_id,
            EntityIdentifier.confidence,
        ).where(
            EntityIdentifier.canonical_type.in_(tuple(settings.LINKABLE_TYPES))
        )
        if scope is not None:
            stmt = stmt.where(scope)
        rows = (await self.session.execute(stmt)).all()

        # O(1) record -> provenance lookups (linear scans here would be O(n*m))
        record_source: dict[int, int | None] = {}
        record_dataset: dict[int, int | None] = {}
        for _iid, _ctype, _value, rid, sid, did, _conf in rows:
            record_source[rid] = sid
            record_dataset[rid] = did

        uf = UnionFind()
        by_key: dict[tuple[str, str], list[int]] = defaultdict(list)
        for ident_id, ctype, value, rid, _sid, _did, _conf in rows:
            uf.add(rid)
            by_key[(ctype, value)].append(rid)

        # ---- 2. union records sharing an identifier ---------------------
        for _key, members in by_key.items():
            if len(members) < 2:
                continue
            first = members[0]
            for other in members[1:]:
                uf.union(first, other)

        clusters = uf.groups()
        root_to_members = {root: sorted(members) for root, members in clusters.items()}

        # ---- 3. reconcile with existing entities ------------------------
        member_to_root = {
            rid: root for root, members in root_to_members.items() for rid in members
        }
        record_ids = list(member_to_root)

        existing_links: dict[int, int] = {}
        if record_ids:
            link_rows = await self.session.execute(
                select(EntityRecord.source_record_id, EntityRecord.master_entity_id)
                .where(EntityRecord.source_record_id.in_(record_ids))
            )
            existing_links = {r[0]: r[1] for r in link_rows.all()}

        root_to_entity: dict[int, int] = {}
        for rid, eid in existing_links.items():
            root = member_to_root.get(rid)
            if root is None:
                continue
            if root in root_to_entity and root_to_entity[root] != eid:
                # two entities collapsed into one cluster -> merge them
                loser = max(root_to_entity[root], eid)
                winner = min(root_to_entity[root], eid)
                await self._merge_entities(session, loser, winner)
                root_to_entity[root] = winner
            else:
                root_to_entity[root] = eid

        # ---- 4. create entities for brand-new clusters -------------------
        created = 0
        for root, members in root_to_members.items():
            if root in root_to_entity:
                continue
            meta = self._entity_meta(members, rows)
            ent = MasterEntity(
                entity_key=meta["entity_key"],
                display_name=meta["display_name"],
                primary_email=meta["primary_email"],
                primary_phone=meta["primary_phone"],
                confidence=meta["confidence"],
                attributes=meta["attributes"],
                record_count=len(members),
                source_count=meta["source_count"],
                identifier_count=meta["identifier_count"],
                status="active",
            )
            self.session.add(ent)
            await self.session.flush()
            root_to_entity[root] = ent.id
            created += 1

        # ---- 5. rewrite the link table ----------------------------------
        if record_ids:
            await self.session.execute(
                delete(EntityRecord).where(EntityRecord.source_record_id.in_(record_ids))
            )

        links: list[dict[str, Any]] = []
        for root, members in root_to_members.items():
            eid = root_to_entity[root]
            for rid in members:
                links.append(
                    {
                        "master_entity_id": eid,
                        "source_record_id": rid,
                        "dataset_id": record_dataset.get(rid),
                        "source_id": record_source.get(rid),
                        "discovered_at_hop": 0,
                        "matched_on_type": None,
                        "match_method": "cluster",
                        "confidence": 1.0,
                    }
                )
        if links:
            await self.session.execute(pg_insert(EntityRecord).values(links))
            self.session.expunge_all()

        # ---- 6. stamp identifiers + refresh entity rollups ---------------
        await self._stamp_identifiers(record_ids)
        updated = await self._refresh_entity_rollups(list(root_to_entity.values()))

        # ---- 7. prune entities that lost every member ---------------------
        # Runs after the links are rewritten, so an entity that still has
        # members is never a candidate. Deleting a source cascades its records
        # and identifiers away, which can leave the entity itself behind.
        pruned = await self._prune_empty_entities()

        await self.session.commit()
        return {
            "clusters": len(root_to_members),
            "entities_created": created,
            "entities_updated": updated,
            "entities_pruned": pruned,
            "records_indexed": len(record_ids),
            "identifiers_indexed": len(rows),
            "duration_ms": int((time.perf_counter() - t0) * 1000),
        }

    async def _prune_empty_entities(self) -> int:
        """Delete master entities that have no members left, anywhere.

        An entity is only removed when it has no ``entity_records`` *and* no
        ``entity_identifiers`` pointing at it, so a partially built entity is
        never dropped mid-import.
        """
        result = await self.session.execute(
            delete(MasterEntity).where(
                ~exists(
                    select(EntityRecord.id).where(
                        EntityRecord.master_entity_id == MasterEntity.id
                    )
                ),
                ~exists(
                    select(EntityIdentifier.id).where(
                        EntityIdentifier.master_entity_id == MasterEntity.id
                    )
                ),
            )
        )
        pruned = int(result.rowcount or 0)
        if pruned:
            logger.info("pruned %d stale master entities with no members", pruned)
        return pruned

    # ------------------------------------------------------------------ #
    async def _stamp_identifiers(self, record_ids: Sequence[int]) -> None:
        await self.session.execute(
            text(
                """
                UPDATE entity_identifiers ei
                SET master_entity_id = er.master_entity_id
                FROM entity_records er
                WHERE er.source_record_id = ei.source_record_id
                  AND ei.source_record_id = ANY(:ids)
                  AND (ei.master_entity_id IS DISTINCT FROM er.master_entity_id)
                """
            ),
            {"ids": list(record_ids)},
        )

    async def _refresh_entity_rollups(self, entity_ids: Sequence[int]) -> int:
        if not entity_ids:
            return 0
        await self.session.execute(
            text(
                """
                UPDATE master_entities me
                SET record_count = s.cnt,
                    source_count = s.src,
                    identifier_count = s.idc,
                    last_seen = now()
                FROM (
                    SELECT er.master_entity_id AS eid,
                           count(*)::int AS cnt,
                           count(DISTINCT er.dataset_id)::int AS src,
                           (SELECT count(*) FROM entity_identifiers ei
                             WHERE ei.master_entity_id = er.master_entity_id)::int AS idc
                    FROM entity_records er
                    WHERE er.master_entity_id = ANY(:ids)
                    GROUP BY er.master_entity_id
                ) s
                WHERE me.id = s.eid
                """
            ),
            {"ids": list(entity_ids)},
        )
        return len(entity_ids)

    async def _merge_entities(
        self, session: AsyncSession, loser: int, winner: int
    ) -> None:
        """Fold ``loser`` into ``winner`` (both already linked to the cluster)."""
        await session.execute(
            update(EntityRecord)
            .where(EntityRecord.master_entity_id == loser)
            .values(master_entity_id=winner)
        )
        await session.execute(
            update(EntityIdentifier)
            .where(EntityIdentifier.master_entity_id == loser)
            .values(master_entity_id=winner)
        )
        winner_entity = await session.get(MasterEntity, winner)
        loser_entity = await session.get(MasterEntity, loser)
        if winner_entity and loser_entity:
            winner_entity.attributes = _merge_attributes(
                winner_entity.attributes, loser_entity.attributes
            )
            await session.flush()
        await session.delete(loser_entity)

    def _entity_meta(
        self, members: Sequence[int], rows: Sequence[tuple]
    ) -> dict[str, Any]:
        member_set = set(members)
        weights = settings.IDENTIFIER_WEIGHTS

        best: dict[str, tuple[float, str]] = {}
        attributes: dict[str, list[Any]] = defaultdict(list)
        value_counter: dict[tuple[str, str], int] = defaultdict(int)
        sources: set[int] = set()
        datasets: set[int] = set()

        for _iid, ctype, value, rid, sid, did, conf in rows:
            if rid not in member_set:
                continue
            if sid:
                sources.add(sid)
            if did:
                datasets.add(did)
            value_counter[(ctype, value)] += 1
            score = weights.get(ctype, 0.4) * (conf or 1.0)
            # frequency breaks ties: the value seen in the most records wins
            score += min(value_counter[(ctype, value)], 5) * 0.01
            if ctype not in best or score > best[ctype][0]:
                best[ctype] = (score, value)
            if value not in attributes[ctype]:
                attributes[ctype].append(value[:512])

        primary_email = best.get("email", (0, None))[1]
        primary_phone = best.get("phone", (0, None))[1]
        display_name = best.get("name", (0, None))[1] or primary_email or primary_phone
        if display_name is None:
            display_name = f"entity-{min(members)}"

        confidence = round(
            min(1.0, max((s for s, _ in best.values()), default=0.0)), 4
        )

        return {
            "entity_key": (primary_email or primary_phone or f"rec-{min(members)}")[:128],
            "display_name": (display_name or "")[:512] or None,
            "primary_email": primary_email,
            "primary_phone": primary_phone,
            "confidence": confidence,
            "attributes": {k: v[:50] for k, v in attributes.items()},
            "source_count": len(sources),
            "identifier_count": sum(value_counter.values()),
        }


def _merge_attributes(
    a: dict[str, Any] | None, b: dict[str, Any] | None
) -> dict[str, Any]:
    out: dict[str, list[Any]] = defaultdict(list)
    for src in (a or {}, b or {}):
        for k, v in src.items():
            vals = v if isinstance(v, list) else [v]
            for item in vals:
                if item not in out[k]:
                    out[k].append(item)
    return {k: v[:50] for k, v in out.items()}


# --------------------------------------------------------------------------- #
# record -> entity persistence for on-demand search results
# --------------------------------------------------------------------------- #
async def persist_resolution(
    session: AsyncSession, result: ResolutionResult, query: str
) -> int | None:
    """Write a search's outcome so the timeline survives the request.

    Ensures the found records belong to (at most) one master entity, creating or
    merging as required, and stores per-record provenance.
    """
    if not result.record_ids:
        return None

    entity_ids = result.entities_touched
    if not entity_ids:
        return None
    entity_id = entity_ids[0]

    # collapse the other touched entities into the winner
    for other in entity_ids[1:]:
        await _collapse_entities(session, entity_id, other)

    if len(entity_ids) > 1:
        result.entities_touched = [entity_id] + entity_ids[1:]

    hop_of = await _recompute_hop_assignment(session, result)

    await session.execute(
        delete(EntityRecord).where(
            and_(
                EntityRecord.master_entity_id == entity_id,
                EntityRecord.source_record_id.in_(result.record_ids),
            )
        )
    )
    prov_rows = await session.execute(
        select(SourceRecord.id, SourceRecord.source_id, SourceRecord.dataset_id).where(
            SourceRecord.id.in_(result.record_ids)
        )
    )
    provenance = {row[0]: (row[1], row[2]) for row in prov_rows.all()}
    links = [
        {
            "master_entity_id": entity_id,
            "source_record_id": rid,
            "dataset_id": provenance.get(rid, (None, None))[1],
            "source_id": provenance.get(rid, (None, None))[0],
            "discovered_at_hop": hop_of.get(rid, 0),
            "match_method": "identifier",
            "confidence": 1.0,
        }
        for rid in result.record_ids
    ]
    if links:
        await session.execute(pg_insert(EntityRecord).values(links))

    await session.execute(
        text(
            """
            UPDATE entity_identifiers ei
            SET master_entity_id = :eid
            WHERE ei.source_record_id = ANY(:ids)
            """
        ),
        {"eid": entity_id, "ids": result.record_ids},
    )
    await session.commit()
    return entity_id


async def _recompute_hop_assignment(
    session: AsyncSession, result: ResolutionResult
) -> dict[int, int]:
    """Re-run the frontier expansion to recover which hop found each record."""
    hop_of: dict[int, int] = {}
    searched: set[tuple[str, str]] = set()
    frontier: set[tuple[str, str]] = {
        (result.seed_type, result.seed_value or "")
    }
    resolver = ProgressiveResolver(session)
    hop_no = 0
    while frontier and hop_no < (result.hop_count or settings.MAX_BFS_DEPTH):
        hop_no += 1
        batch = list(frontier)[: settings.BFS_BATCH_SIZE]
        recs = await resolver._records_for_identifiers(batch)
        reasons = await resolver._match_reason(recs, batch) if recs else {}
        for rid in recs:
            if rid in hop_of:
                continue
            reason = reasons.get(rid, {})
            if reason.get("canonical_type") in settings.LINKABLE_TYPES:
                hop_of[rid] = hop_no
        searched.update(batch)
        next_frontier: set[tuple[str, str]] = set()
        for rid in recs:
            if rid in hop_of:
                for item in await resolver._identifiers_for_records([rid]):
                    key = (item["canonical_type"], item["normalized_value"])
                    if item["canonical_type"] in settings.LINKABLE_TYPES and key not in searched:
                        next_frontier.add(key)
        frontier = next_frontier
    return hop_of


async def _collapse_entities(session: AsyncSession, winner: int, loser: int) -> None:
    await session.execute(
        update(EntityRecord)
        .where(EntityRecord.master_entity_id == loser)
        .values(master_entity_id=winner)
    )
    await session.execute(
        update(EntityIdentifier)
        .where(EntityIdentifier.master_entity_id == loser)
        .values(master_entity_id=winner)
    )
    loser_entity = await session.get(MasterEntity, loser)
    winner_entity = await session.get(MasterEntity, winner)
    if winner_entity and loser_entity:
        winner_entity.attributes = _merge_attributes(
            winner_entity.attributes, loser_entity.attributes
        )
    if loser_entity:
        await session.delete(loser_entity)
    await session.flush()


# convenience re-exports used by the API layer
__all__ = [
    "ProgressiveResolver",
    "ClusterIndexer",
    "ResolutionResult",
    "Hop",
    "UnionFind",
    "infer_seed_type",
    "persist_resolution",
    "IDENTIFIABLE_TYPES",
]
