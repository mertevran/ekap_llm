from __future__ import annotations

import json
import shutil
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path.cwd()
CONFIG_DIR = PROJECT_ROOT / "config" / "isbak"
CLASSIFIER_PATH = (
    PROJECT_ROOT
    / "app"
    / "classification"
    / "isbak_tender_profile_classifier.py"
)
CLASSIFY_SCRIPT_PATH = (
    PROJECT_ROOT
    / "scripts"
    / "classify_isbak_tenders_v2.py"
)
TEST_PATH = (
    PROJECT_ROOT
    / "tests"
    / "unit"
    / "test_isbak_tender_profile_classifier_v2_2.py"
)


PROFILE_PATCHES = {
    "ENT-05": {
        "negatif_terimler": [
            "genel elektrik malzemesi",
            "elektrik malzemesi alımı",
            "ampul alımı",
            "kablo alımı",
            "priz",
            "anahtar",
            "sigorta malzemesi",
            "elektrik sarf malzemesi",
        ],
    },
    "TEK-01": {
        "guclu_terimler": [
            "yazılım geliştirme",
            "bilgi yönetim sistemi",
            "yönetim bilgi sistemi",
            "sipariş yönetim platformu",
            "sipariş yönetim sistemi",
            "kurumsal yazılım platformu",
            "sistem entegrasyonu",
            "veri tabanı",
            "web uygulaması",
            "mobil uygulama",
            "uygulama programlama arayüzü",
        ],
        "destekleyici_terimler": [
            "raporlama sistemi",
            "gösterge paneli",
            "veri aktarımı",
            "yazılım bakım",
            "yazılım teknik desteği",
            "sunucu yazılım hizmeti",
            "kurumsal uygulama",
            "platform kurulumu",
        ],
        "negatif_terimler": [
            "bilgisayar sarf malzemesi",
            "bilgisayar sarf malzemeleri",
            "kartuş alımı",
            "toner alımı",
            "hazır ofis yazılımı",
            "antivirüs lisansı",
        ],
    },
    "TEK-02": {
        "negatif_terimler": [
            "genel elektrik malzemesi",
            "elektrik malzemesi alımı",
            "bilgisayar sarf malzemesi",
            "bilgisayar sarf malzemeleri",
            "derin kuyu pompası",
            "derin kuyu pompaları",
            "pompa montajı",
            "mekanik tesisat",
            "kablo alımı",
            "priz",
            "anahtar",
            "sigorta malzemesi",
            "beyaz eşya",
            "mobilya donanımı",
        ],
    },
    "TEK-03": {
        "negatif_terimler": [
            "bilgisayar sarf malzemesi",
            "bilgisayar sarf malzemeleri",
            "kartuş",
            "toner",
            "klavye",
            "fare alımı",
            "adaptör alımı",
        ],
    },
    "TEK-04": {
        "guclu_terimler": [
            "siber güvenlik",
            "bilgi güvenliği",
            "felaket kurtarma",
            "felaket kurtarma sistemi",
            "yedekleme yazılımı",
            "veri yedekleme sistemi",
            "yedekleme altyapısı",
            "sunucu güvenliği",
            "güvenlik duvarı",
            "kayıt bütünlüğü",
        ],
        "destekleyici_terimler": [
            "sunucu bakım",
            "sunucu bakım hizmeti",
            "yedekleme hizmeti",
            "erişim kontrolü",
            "yetkilendirme",
            "güvenlik günlüğü",
            "yedekleme",
            "antivirüs",
        ],
        "negatif_terimler": [
            "özel güvenlik personeli",
            "bilgisayar sarf malzemesi",
            "bilgisayar sarf malzemeleri",
        ],
    },
    "OPS-01": {
        "negatif_terimler": [
            "bilgisayar sarf malzemesi",
            "bilgisayar sarf malzemeleri",
            "derin kuyu pompası",
            "derin kuyu pompaları",
            "pompa montajı",
            "mekanik tesisat",
            "genel elektrik malzemesi",
        ],
    },
    "OPS-02": {
        "guclu_terimler": [
            "bakım onarım hizmeti",
            "teknik destek hizmeti",
            "yazılım bakım ve destek",
            "sunucu bakım hizmeti",
            "arıza müdahalesi",
        ],
        "destekleyici_terimler": [
            "koruyucu bakım",
            "yedek parça",
            "hizmet seviyesi",
            "uzaktan destek",
            "teknik destek",
            "24 aylık teknik destek",
            "yazılım güncelleme",
        ],
        "negatif_terimler": [
            "bina bakım onarım",
            "mezarlık bakım onarım",
            "park bakım onarım",
            "yol bakım onarım",
            "derin kuyu pompası",
            "bilgisayar sarf malzemesi",
        ],
    },
}


def merge_unique(existing: list[str], incoming: list[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()

    for value in [*existing, *incoming]:
        normalized = str(value).strip()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        result.append(normalized)

    return result


def create_backup() -> Path:
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_dir = PROJECT_ROOT / "backups" / f"isbak_v2_2_{stamp}"
    backup_dir.mkdir(parents=True, exist_ok=True)

    shutil.copy2(
        CLASSIFIER_PATH,
        backup_dir / CLASSIFIER_PATH.name,
    )

    if CLASSIFY_SCRIPT_PATH.exists():
        shutil.copy2(
            CLASSIFY_SCRIPT_PATH,
            backup_dir / CLASSIFY_SCRIPT_PATH.name,
        )

    shutil.copytree(
        CONFIG_DIR,
        backup_dir / "isbak_config",
    )

    return backup_dir


def patch_profiles() -> int:
    registry_path = CONFIG_DIR / "profile_registry.json"
    registry = json.loads(registry_path.read_text(encoding="utf-8"))

    updated = 0

    for entry in registry["profiller"]:
        code = entry["profil_kodu"]

        if code not in PROFILE_PATCHES:
            continue

        profile_path = CONFIG_DIR / entry["profil_dosyasi"]
        profile = json.loads(profile_path.read_text(encoding="utf-8"))

        signals = dict(
            profile.get("ihale_kategori_sinyalleri", {})
        )

        for field_name, values in PROFILE_PATCHES[code].items():
            signals[field_name] = merge_unique(
                list(signals.get(field_name, [])),
                values,
            )

        profile["ihale_kategori_sinyalleri"] = signals

        profile_path.write_text(
            json.dumps(
                profile,
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )

        updated += 1

    return updated


def patch_classifier() -> None:
    text = CLASSIFIER_PATH.read_text(encoding="utf-8")

    text = text.replace(
        'CLASSIFIER_VERSION = "isbak_profile_classifier_v2_1"',
        'CLASSIFIER_VERSION = "isbak_profile_classifier_v2_2"',
    )

    old_block = '''        selected_matches = [
            item
            for item in matches
            if item.status in {"guclu_eslesme", "kosullu_eslesme"}
        ]
        review_matches = [
            item
            for item in matches
            if item.status == "inceleme_gerekli"
        ]

        profile_codes = [item.profile_code for item in selected_matches]
'''

    new_block = '''        selected_matches = [
            item
            for item in matches
            if item.status in {"guclu_eslesme", "kosullu_eslesme"}
        ]
        review_matches = [
            item
            for item in matches
            if item.status == "inceleme_gerekli"
        ]

        # AUS, ENT, PLN ve TEK ana teknik profillerdir.
        # OPS profilleri kurulum, bakım ve danışmanlık gibi destek
        # yetkinliklerini temsil eder. Teknik profil bulunduğunda
        # OPS profili birincil profile çıkmamalıdır.
        def profile_priority(profile_code: str) -> int:
            return 1 if profile_code.startswith("OPS-") else 0

        selected_matches.sort(
            key=lambda item: (
                profile_priority(item.profile_code),
                -item.raw_score,
                item.profile_code,
            )
        )

        profile_codes = [item.profile_code for item in selected_matches]
'''

    if old_block not in text:
        raise RuntimeError(
            "Sınıflandırıcıdaki profil seçim bloğu bulunamadı. "
            "Dosya beklenen V2.1 yapısında olmayabilir."
        )

    text = text.replace(old_block, new_block)

    CLASSIFIER_PATH.write_text(text, encoding="utf-8")


def patch_classification_script() -> None:
    if not CLASSIFY_SCRIPT_PATH.exists():
        return

    text = CLASSIFY_SCRIPT_PATH.read_text(encoding="utf-8")

    old_import = '''from app.classification.isbak_tender_profile_classifier import (
    IsbakTenderProfileClassifier,
)
'''

    new_import = '''from app.classification.isbak_tender_profile_classifier import (
    CLASSIFIER_VERSION,
    IsbakTenderProfileClassifier,
)
'''

    if old_import in text and "CLASSIFIER_VERSION" not in text:
        text = text.replace(old_import, new_import)

    text = text.replace(
        '"classifier_version": "isbak_profile_classifier_v2"',
        '"classifier_version": CLASSIFIER_VERSION',
    )

    CLASSIFY_SCRIPT_PATH.write_text(text, encoding="utf-8")


def create_tests() -> None:
    TEST_PATH.parent.mkdir(parents=True, exist_ok=True)

    test_content = '''from types import SimpleNamespace

from app.classification.isbak_tender_profile_classifier import (
    CLASSIFIER_VERSION,
    IsbakTenderProfileClassifier,
)
from app.company_profiles import IsbakProfileLoader


def tender(title: str, scope: str = "", okas=None):
    return SimpleNamespace(
        id="fixture-v2-2",
        ikn="TEST/V2-2",
        adi=title,
        kapsam=scope,
        ihale_turu="Hizmet",
        ihale_usulu="Açık",
        idare_adi="Test İdaresi",
        announcements=[],
        characteristics=[],
        okas_codes=[
            SimpleNamespace(kod=code, ad=name)
            for code, name in (okas or [])
        ],
    )


def classifier():
    return IsbakTenderProfileClassifier(
        IsbakProfileLoader("config/isbak")
    )


def test_classifier_version_is_v2_2():
    assert CLASSIFIER_VERSION == "isbak_profile_classifier_v2_2"


def test_computer_consumables_are_irrelevant():
    result = classifier().classify(
        tender(
            "Bilgisayar Sarf Malzemeleri",
            "Klavye, fare, adaptör, kablo ve toner alımı",
        )
    )

    assert result.profile_codes == []
    assert result.primary_profile_code is None


def test_deep_well_pump_is_not_technical_profile():
    result = classifier().classify(
        tender(
            "Derin Kuyu Pompaları Montajı ve Devreye Alınması",
            "Mekanik tesisat ve pompa montajı yapılacaktır.",
        )
    )

    assert result.profile_codes == []
    assert result.primary_profile_code is None


def test_server_backup_service_is_candidate():
    result = classifier().classify(
        tender(
            "24 Aylık Sunucu Bakım Onarım ve Yedekleme Yazılım Hizmeti",
            "Sunucu bakım hizmeti, veri yedekleme sistemi ve "
            "yedekleme yazılımı teknik desteği",
            okas=[("72260000", "Yazılım hizmetleri")],
        )
    )

    assert "TEK-04" in result.profile_codes
    assert result.overall_status in {
        "guclu_eslesme",
        "kosullu_eslesme",
    }


def test_software_platform_is_primary_over_ops():
    result = classifier().classify(
        tender(
            "Çoklu Satıcı Destekli Sipariş Yönetim Platformu "
            "Kurulum ve Teknik Destek",
            "Kurumsal yazılım platformu kurulumu ve teknik destek hizmeti",
        )
    )

    assert result.primary_profile_code == "TEK-01"
    assert "OPS-02" in result.profile_codes
'''

    TEST_PATH.write_text(test_content, encoding="utf-8")


def main() -> int:
    required_paths = [
        CONFIG_DIR / "profile_registry.json",
        CLASSIFIER_PATH,
    ]

    for path in required_paths:
        if not path.exists():
            raise FileNotFoundError(
                f"Gerekli dosya bulunamadı: {path}"
            )

    backup_dir = create_backup()
    updated_profiles = patch_profiles()
    patch_classifier()
    patch_classification_script()
    create_tests()

    print("İSBAK V2.2 düzeltmesi başarıyla uygulandı.")
    print(f"Güncellenen profil sayısı: {updated_profiles}")
    print(f"Yedek klasörü: {backup_dir}")
    print(f"Yeni sınama dosyası: {TEST_PATH}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
