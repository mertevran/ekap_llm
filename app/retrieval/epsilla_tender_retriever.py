"""Epsilla vektör veritabanı destekli ihale getirici (geçici test adaptörü).

Bu modül yalnızca Epsilla teknoloji testi için kullanılır.
Üretim sistemindeki FAISS tabanlı IsbakTenderRetriever veya FaissVectorStore
dosyaları bu modül tarafından değiştirilmez veya üzerine yazılmaz.

Karar hattının geri kalanının Epsilla kullandığını bilmesine gerek yoktur.
Bu sınıf mevcut IsbakTenderRetriever ile aynı TenderSearchResult / ChunkEvidence
çıktı sözleşmesini sağlar.

Mimari not:
  Retrieval hedefi, AYNI İHALENİN kendi chunk'larını Epsilla'dan geri getirmektir.
  Sorgu vektörü, ihale başlığı + kapsam + OKAS metni üzerinden BGE-M3 ile üretilir.
  Mesafe metriği: Epsilla IndexFlatIP inner-product kullanır; dönen @dist değeri
  inner-product distance'dır. Normalize edilmiş vektörlerde IP = cosine similarity.
  Bu nedenle benzerlik skoru doğrudan @dist değeri olarak kullanılır (1 - dist DEĞİL).

Puanlama:
  Mevcut ScoreAggregator (app.matching.score_aggregator) yeniden kullanılır.
  Profil meta (strong_terms, negative_terms, okas_prefixes) IsbakProfileLoader'dan yüklenir.
  negative_penalty=0.0 sabit değeri KULLANILMAZ; ScoreAggregator gerçek profil
  negatif terimlerini uygular.
"""

from __future__ import annotations

import json
import logging
import os
import re
import unicodedata
from typing import Any, Sequence

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Mevcut retriever'dan dataclass'ları yeniden kullan (duplicate etme)
# ---------------------------------------------------------------------------
from app.retrieval.isbak_tender_retriever import (
    ChunkEvidence,
    ScoreBreakdown,
    TenderSearchResult,
)

# ---------------------------------------------------------------------------
# Mevcut ScoreAggregator yeniden kullan
# ---------------------------------------------------------------------------
from app.matching.score_aggregator import ScoreAggregator, build_query_terms
from app.vector_store.faiss_vector_reader import KNOWN_SECTION_TYPES

# ---------------------------------------------------------------------------
# Epsilla bağlantı ayarları
# .env'den EPSILLA_HOST / EPSILLA_PORT okunur.
# PostgreSQL tarafında asla varsayılan kullanılmaz.
# ---------------------------------------------------------------------------
_DEFAULT_EPSILLA_HOST = "localhost"
_DEFAULT_EPSILLA_PORT = 8888

_TOKEN_PATTERN = re.compile(r"[a-zçğışöü0-9]+", re.IGNORECASE | re.UNICODE)
_STOPWORDS = frozenset(
    {
        "ve", "veya", "ile", "için", "bir", "bu", "şu", "o",
        "alımı", "alım", "satın", "hizmet", "hizmeti", "işi", "iş",
        "malzeme", "malzemesi", "ihalesi", "ihale", "yapım", "temini", "temin",
    }
)


def _normalize(text: str) -> str:
    normalized = unicodedata.normalize("NFKC", str(text))
    normalized = normalized.translate(str.maketrans({"I": "ı", "İ": "i"}))
    return normalized.lower().replace("\u0307", "").strip()


def _tokenize(text: str) -> list[str]:
    return _TOKEN_PATTERN.findall(_normalize(text))


def _query_terms_simple(query: str) -> tuple[str, ...]:
    tokens = _tokenize(query)
    meaningful = [t for t in tokens if t not in _STOPWORDS and len(t) >= 3]
    return tuple(dict.fromkeys(meaningful or tokens))


# ---------------------------------------------------------------------------
# Epsilla bağlantı yardımcı sınıfı
# ---------------------------------------------------------------------------


class EpsillaClient:
    """pyepsilla.vectordb.Client üzerinde ince bir sarmalayıcı.

    Bağımlılık isteğe bağlıdır; yüklü değilse net hata mesajı verilir.

    Mesafe metriği notu:
      Epsilla varsayılan olarak Inner Product (IP) mesafesi kullanır.
      Normalize edilmiş BGE-M3 vektörleriyle IP = cosine similarity.
      Dolayısıyla @dist değeri doğrudan benzerlik skoru olarak kullanılır;
      1 - @dist dönüşümü YAPILMAZ.
    """

    def __init__(
        self,
        *,
        host: str | None = None,
        port: int | None = None,
    ) -> None:
        try:
            from pyepsilla import vectordb  # type: ignore[import]
        except ImportError as exc:
            raise ImportError(
                "pyepsilla paketi kurulu değil. Kurmak için:\n"
                "  .venv/bin/pip install pyepsilla\n"
                "veya  pip install pyepsilla"
            ) from exc

        self.host = host or os.environ.get("EPSILLA_HOST", _DEFAULT_EPSILLA_HOST)
        self.port = port or int(os.environ.get("EPSILLA_PORT", str(_DEFAULT_EPSILLA_PORT)))
        self._client = vectordb.Client(
            host=str(self.host),
            port=str(self.port),
        )
        self._vectordb = vectordb

    @property
    def raw(self) -> Any:
        return self._client

    def connect(self) -> None:
        """Epsilla servisine bağlan ve EkapTestDB'yi yükle."""
        status_code, response = self._client.load_db(
            db_name="EkapTestDB",
            db_path="/tmp/EkapTestDB",
        )
        if status_code not in (200, 201, 409):
            raise RuntimeError(
                f"Epsilla DB yüklenemedi (status={status_code}): {response}"
            )
        logger.info(f"[EPSILLA_CONNECT] DB yüklendi: EkapTestDB (status={status_code})")

    def use_db(self, db_name: str = "EkapTestDB") -> None:
        self._client.use_db(db_name)

    def create_table_if_not_exists(
        self,
        table_name: str,
        vector_dimension: int,
    ) -> None:
        """TenderChunks tablosunu oluşturur (zaten varsa devam eder)."""
        status_code, response = self._client.create_table(
            table_name=table_name,
            table_fields=[
                {"name": "id",           "dataType": "STRING", "primaryKey": True},
                {"name": "tender_id",    "dataType": "STRING"},
                {"name": "ikn",          "dataType": "STRING"},
                {"name": "title",        "dataType": "STRING"},
                {"name": "section",      "dataType": "STRING"},   # section_type
                {"name": "chunk_id",     "dataType": "STRING"},
                {"name": "section_id",   "dataType": "STRING"},
                {"name": "text",         "dataType": "STRING"},
                {"name": "authority",    "dataType": "STRING"},
                {"name": "il",           "dataType": "STRING"},
                {"name": "ihale_tarihi", "dataType": "STRING"},
                {"name": "ihale_turu",   "dataType": "STRING"},
                # JSON-serialized list olarak saklanır
                {"name": "okas_codes",   "dataType": "STRING"},
                {"name": "profile_codes", "dataType": "STRING"},
                {
                    "name": "embedding",
                    "dataType": "VECTOR_FLOAT",
                    "dimensions": vector_dimension,
                },
            ],
        )
        if status_code == 409:
            logger.info(f"[EPSILLA_CONNECT] Tablo zaten var: {table_name}")
        elif status_code not in (200, 201):
            raise RuntimeError(
                f"Epsilla tablo oluşturulamadı (status={status_code}): {response}"
            )
        else:
            logger.info(
                f"[EPSILLA_CONNECT] Tablo oluşturuldu: {table_name} (dim={vector_dimension})"
            )

    def insert_records(
        self,
        table_name: str,
        records: list[dict[str, Any]],
    ) -> None:
        """Kayıtları toplu ekler."""
        status_code, response = self._client.insert(
            table_name=table_name,
            records=records,
        )
        if status_code not in (200, 201):
            raise RuntimeError(
                f"Epsilla insert başarısız (status={status_code}): {response}"
            )

    def query(
        self,
        table_name: str,
        query_field: str,
        query_vector: list[float],
        limit: int,
        response_fields: list[str] | None = None,
        filter_expr: str | None = None,
    ) -> list[dict[str, Any]]:
        """Yakın komşu araması yapar.

        Epsilla dönen @dist değeri Inner Product mesafesidir.
        Normalize edilmiş vektörlerde @dist = cosine_similarity.
        Yani @dist değeri doğrudan benzerlik skoru olarak kullanılır.
        """
        fields = response_fields or [
            "id", "tender_id", "ikn", "title", "section", "chunk_id",
            "section_id", "text", "authority", "il", "ihale_tarihi",
            "ihale_turu", "okas_codes", "profile_codes",
        ]
        kwargs: dict[str, Any] = dict(
            table_name=table_name,
            query_field=query_field,
            query_vector=query_vector,
            limit=limit,
            response_fields=fields,
            with_distance=True,
        )
        if filter_expr:
            kwargs["filter"] = filter_expr

        status_code, response = self._client.query(**kwargs)
        if status_code not in (200, 201):
            raise RuntimeError(
                f"Epsilla sorgu başarısız (status={status_code}): {response}"
            )
        result_obj = response.get("result", []) if isinstance(response, dict) else []
        return result_obj if isinstance(result_obj, list) else []

    def drop_table(self, table_name: str) -> None:
        self._client.drop_table(table_name)

    def drop_db(self, db_name: str) -> None:
        self._client.drop_db(db_name)


# ---------------------------------------------------------------------------
# Ana Epsilla Retriever — IsbakTenderRetriever ile aynı çıktı sözleşmesi
# ---------------------------------------------------------------------------


class EpsillaTenderRetriever:
    """Epsilla tabanlı ihale chunk getirici — geçici Epsilla testi için.

    Hedef: AYNI İHALENİN en alakalı chunk'larını Epsilla'dan getirmek.
    Sorgu → aynı ihale IKN/tender_id filtresi → top-k chunk.

    Puanlama mevcut ScoreAggregator ile yapılır; profil meta (strong_terms,
    negative_terms, okas_prefixes) IsbakProfileLoader üzerinden yüklenir.

    Çıktı tipi IsbakTenderRetriever ile aynıdır: list[TenderSearchResult].
    """

    DB_NAME = "EkapTestDB"
    TABLE_NAME = "TenderChunks"

    def __init__(
        self,
        *,
        epsilla_client: EpsillaClient,
        embedder: Any,              # BgeM3Embedder veya EmbedderProtocol uyumlu
        scorer: ScoreAggregator,   # Mevcut ScoreAggregator — inject edilir
        top_k: int = 40,
        max_chunks_per_tender: int = 4,
    ) -> None:
        self.client = epsilla_client
        self.embedder = embedder
        self.scorer = scorer
        self.top_k = top_k
        self.max_chunks_per_tender = max_chunks_per_tender

    # ------------------------------------------------------------------
    # Ana API — aynı ihalenin chunk'larını Epsilla'dan getirir
    # ------------------------------------------------------------------

    def retrieve_for_tender(
        self,
        *,
        query: str,
        tender_id: str,
        ikn: str,
        profile_meta: dict[str, Any] | None = None,
    ) -> TenderSearchResult | None:
        """Belirtilen ihalenin en alakalı chunk'larını Epsilla'dan getirir.

        Args:
            query: İhale başlığı + kapsam + OKAS metni birleşimi.
            tender_id: Filtreleme için kullanılacak tender_id.
            ikn: Filtreleme için yedek IKN.
            profile_meta: ScoreAggregator için profil meta verisi.
              {strong_terms, negative_terms, okas_prefixes, okas_text_support_required}
        """
        query = query.strip()
        if not query:
            raise ValueError("Sorgu boş olamaz.")

        vectors = self.embedder.embed([query])
        query_vector = vectors[0]
        query_terms = build_query_terms(query)

        # Epsilla'ya tender_id veya ikn filtresi ile sorgu at
        # Epsilla filter sözdizimi: "field = 'value'"
        # Önce tender_id ile dene, çalışmazsa filtre olmadan al ve Python'da filtrele
        raw_chunks = self._query_with_filter(
            query_vector=query_vector,
            tender_id=tender_id,
            ikn=ikn,
        )

        if not raw_chunks:
            logger.warning(
                f"[EPSILLA_RETRIEVAL] Chunk bulunamadı: tender_id={tender_id}, ikn={ikn}"
            )
            return None

        # chunk'ları normalize et
        normalized = self._normalize_chunks(raw_chunks)
        # En yüksek skorlu chunk'ları al (max_chunks_per_tender adet)
        normalized.sort(key=lambda c: c["_score"], reverse=True)
        top_chunks = normalized[: self.max_chunks_per_tender]

        return self._build_result(
            tender_id=tender_id,
            ikn=ikn,
            chunks=top_chunks,
            query=query,
            query_terms=query_terms,
            profile_meta=profile_meta or {},
        )

    # Geriye uyumluluk: eski retrieve() imzası — evidence olarak Epsilla'yı kullan
    def retrieve(self, query: str, limit: int | None = None) -> list[TenderSearchResult]:
        """Tüm koleksiyonda arama yapar (filtre olmadan).
        Yalnızca FAISS retriever ile karşılaştırma testleri için kullanılır.
        """
        query = query.strip()
        if not query:
            raise ValueError("Sorgu boş olamaz.")

        eff_limit = limit if limit is not None else 30
        vectors = self.embedder.embed([query])
        query_vector = vectors[0]
        query_terms = build_query_terms(query)

        raw_chunks = self.client.query(
            table_name=self.TABLE_NAME,
            query_field="embedding",
            query_vector=query_vector,
            limit=self.top_k,
        )

        normalized = self._normalize_chunks(raw_chunks)
        groups = self._group_by_tender(normalized)

        results: list[TenderSearchResult] = []
        for tender_id_key, chunk_list in groups.items():
            # tender_id → ikn çıkar
            ikn_val = chunk_list[0].get("ikn", tender_id_key) if chunk_list else tender_id_key
            result = self._build_result(
                tender_id=tender_id_key,
                ikn=str(ikn_val),
                chunks=chunk_list,
                query=query,
                query_terms=query_terms,
                profile_meta={},
            )
            if result:
                results.append(result)

        results.sort(key=lambda r: r.scores.final, reverse=True)
        return results[:eff_limit]

    # ------------------------------------------------------------------
    # İç yardımcılar
    # ------------------------------------------------------------------

    def _query_with_filter(
        self,
        *,
        query_vector: list[float],
        tender_id: str,
        ikn: str,
    ) -> list[dict[str, Any]]:
        """Önce tender_id filtresiyle sorgula; boş gelirse ikn ile tekrar dene;
        o da boş gelirse filtre olmadan al ve Python'da filtrele."""

        # Deneme 1: tender_id filtresi
        try:
            results = self.client.query(
                table_name=self.TABLE_NAME,
                query_field="embedding",
                query_vector=query_vector,
                limit=self.top_k,
                filter_expr=f"tender_id = '{tender_id}'",
            )
            if results:
                logger.debug(
                    f"[EPSILLA_RETRIEVAL] tender_id filtresiyle {len(results)} chunk döndü"
                )
                return results
        except Exception as exc:
            logger.debug(f"[EPSILLA_RETRIEVAL] tender_id filtresi başarısız: {exc}")

        # Deneme 2: ikn filtresi
        try:
            results = self.client.query(
                table_name=self.TABLE_NAME,
                query_field="embedding",
                query_vector=query_vector,
                limit=self.top_k,
                filter_expr=f"ikn = '{ikn}'",
            )
            if results:
                logger.debug(
                    f"[EPSILLA_RETRIEVAL] ikn filtresiyle {len(results)} chunk döndü"
                )
                return results
        except Exception as exc:
            logger.debug(f"[EPSILLA_RETRIEVAL] ikn filtresi başarısız: {exc}")

        # Deneme 3: Filtre olmadan al, Python'da filtrele
        logger.info(
            "[EPSILLA_RETRIEVAL] Filtre desteklenmiyor; Python tarafında "
            f"tender_id={tender_id} / ikn={ikn} filtresi uygulanacak."
        )
        try:
            all_results = self.client.query(
                table_name=self.TABLE_NAME,
                query_field="embedding",
                query_vector=query_vector,
                limit=self.top_k * 4,  # Geniş havuz al, Python'da filtrele
            )
            filtered = [
                r for r in all_results
                if str(r.get("tender_id", "")) == tender_id
                or str(r.get("ikn", "")) == ikn
            ]
            logger.debug(
                f"[EPSILLA_RETRIEVAL] Python filtresi: {len(filtered)}/{len(all_results)} chunk"
            )
            return filtered
        except Exception as exc:
            logger.error(f"[EPSILLA_RETRIEVAL] Filtersiz sorgu da başarısız: {exc}")
            return []

    def _normalize_chunks(self, raw_chunks: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Epsilla ham sonuçlarını normalleştirir.

        pyepsilla sorgu sonucunda yakınlık değeri ``@distance`` alanında gelir.
        Epsilla sonuçlarında küçük mesafe daha iyi eşleşme anlamına gelir.

        Mevcut ScoreAggregator sözleşmesinde ise yüksek skor daha iyi eşleşmedir.
        Bu nedenle mesafeyi monoton biçimde [0, 1] aralığında benzerlik skoruna
        dönüştürüyoruz.
        """
        normalized = []

        for chunk in raw_chunks:
            raw_distance = chunk.get("@distance")

            try:
                distance = float(raw_distance)
            except (TypeError, ValueError):
                distance = float("inf")

            score = (
                0.0
                if distance == float("inf")
                else 1.0 / (1.0 + max(0.0, distance))
            )

            # okas_codes ve profile_codes JSON string → list
            for field_name in ("okas_codes", "profile_codes"):
                raw_val = chunk.get(field_name, "[]")
                if isinstance(raw_val, str):
                    try:
                        chunk[field_name] = json.loads(raw_val)
                    except Exception:
                        chunk[field_name] = []

            chunk["_epsilla_distance"] = (
                None if distance == float("inf") else distance
            )
            chunk["_score"] = score
            normalized.append(chunk)

        return normalized

    def _group_by_tender(
        self,
        normalized: list[dict[str, Any]],
    ) -> dict[str, list[dict[str, Any]]]:
        """Chunk'ları tender_id'ye göre grupla; tender başına max chunk uygula."""
        tender_chunks: dict[str, list[dict[str, Any]]] = {}
        for chunk in normalized:
            tender_key = str(chunk.get("tender_id") or chunk.get("ikn") or "").strip()
            if not tender_key:
                continue
            tender_chunks.setdefault(tender_key, []).append(chunk)

        groups: dict[str, list[dict[str, Any]]] = {}
        for tender_key, chunks in tender_chunks.items():
            # Chunk_id bazında tekilleştir
            seen: dict[str, dict[str, Any]] = {}
            for c in chunks:
                cid = str(c.get("chunk_id") or c.get("id") or "")
                if cid not in seen or c["_score"] > seen[cid]["_score"]:
                    seen[cid] = c
            unique = sorted(seen.values(), key=lambda c: c["_score"], reverse=True)
            groups[tender_key] = unique[: self.max_chunks_per_tender]

        return groups

    def _build_result(
        self,
        *,
        tender_id: str,
        ikn: str,
        chunks: list[dict[str, Any]],
        query: str,
        query_terms: tuple[str, ...],
        profile_meta: dict[str, Any],
    ) -> TenderSearchResult | None:
        if not chunks:
            return None

        top_chunk = chunks[0]
        tender_name = str(top_chunk.get("title") or ikn)

        raw_scores = [float(c.get("_score", 0.0)) for c in chunks]

        section_types: list[str] = [
            str(c.get("section") or "")
            for c in chunks
        ]

        # OKAS kodları
        okas_codes: list[str] = []
        for c in chunks:
            raw = c.get("okas_codes", [])
            if isinstance(raw, list):
                okas_codes.extend(str(x) for x in raw if x)

        evidence_texts = [str(c.get("text") or "")[:500] for c in chunks]

        # ScoreAggregator ile puan hesapla (mevcut üretim kodu)
        breakdown = self.scorer.compute(
            raw_scores=raw_scores,
            section_types=section_types,
            okas_codes=okas_codes,
            query_terms=query_terms,
            tender_name=tender_name,
            profile_okas_prefixes=profile_meta.get("okas_prefixes", []),
            strong_terms=profile_meta.get("strong_terms", []),
            negative_terms=profile_meta.get("negative_terms", []),
            evidence_texts=evidence_texts,
            okas_text_support_required=profile_meta.get("okas_text_support_required", False),
        )

        # ScoreBreakdown → mevcut IsbakTenderRetriever ile uyumlu
        scores = ScoreBreakdown(
            max_chunk=breakdown.max_similarity,
            top_chunks_mean=breakdown.top_similarity_mean,
            section_diversity=breakdown.section_diversity,
            okas_support=breakdown.okas_support,
            title_support=breakdown.title_support,
            final=breakdown.final_score,
            negative_penalty=breakdown.negative_term_penalty,
        )

        profile_codes: list[str] = []
        pc = top_chunk.get("profile_codes", [])
        if isinstance(pc, list):
            profile_codes = [str(x) for x in pc if x]

        evidence_chunks = [
            ChunkEvidence(
                chunk_id=str(c.get("chunk_id") or c.get("id") or ""),
                section_id=str(c.get("section_id") or c.get("section") or ""),
                chunk_title=str(c.get("title") or ""),
                semantic_score=round(float(c.get("_score", 0.0)), 4),
                text=str(c.get("text") or ""),
                text_preview=str(c.get("text") or "")[:200],
            )
            for c in chunks
        ]

        return TenderSearchResult(
            tender_id=str(top_chunk.get("tender_id") or tender_id),
            ikn=str(top_chunk.get("ikn") or ikn),
            tender_name=tender_name,
            chunk_title=evidence_chunks[0].chunk_title if evidence_chunks else "",
            primary_profile_code=str(profile_codes[0]) if profile_codes else "",
            profile_codes=profile_codes,
            classification_status="epsilla_test",
            idare_adi=str(top_chunk.get("authority") or ""),
            il=str(top_chunk.get("il") or ""),
            ihale_tarihi=str(top_chunk.get("ihale_tarihi") or ""),
            ihale_turu=str(top_chunk.get("ihale_turu") or ""),
            okas_codes=list(dict.fromkeys(okas_codes)),
            section_ids=list(dict.fromkeys(st for st in section_types if st)),
            scores=scores,
            evidence_chunks=evidence_chunks,
        )
