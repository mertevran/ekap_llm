"""
EKAP İhale MCP sunucusu — SADECE OKUMA.

    python scripts/mcp_sunucu.py            # stdio (Claude Desktop vb.)

=============================================================================
NE YAPAR
=============================================================================
Uzmanın doğal dille ihale sorgulayabilmesi için bir okuma arayüzü açar.
Yedi araç sunar; hepsi `app/mcp/sorgular.py` içindeki düz Python fonksiyonların
ince sarmalayıcısıdır. Buradaki kod yalnızca MCP kablolaması yapar — iş mantığı
orada, çünkü MCP çalışma zamanı olmadan test edilebilmesi gerekiyor.

=============================================================================
KARAR HATTINA DOKUNMAZ
=============================================================================
LLM çağırmaz, retrieval yapmaz, HİÇBİR tabloya yazmaz. Rapor 4.3'ün gerekçesi:
sistemin ölçülebilirliği karar hattının sabit ve deterministik olmasına dayanır.
Bir ajanın kendi bağlamını toplaması bunu bitirir ve CPU'da ihale başına süreyi
29 dakikadan ~2 saate çıkarır.

Uzman burada ADAY BULUR; kararı isterse mevcut hatta verir:
    python scripts/analyze_tender.py <İKN> --sadece-asama1 --ayrintili

=============================================================================
"SQL ENCODER" — text-to-SQL nerede
=============================================================================
`serbest_sorgu` aracı bir `WHERE` ifadesi alır. O ifadeyi ÜRETEN taraf MCP
İSTEMCİSİDİR (Claude vb.). Yani text-to-SQL yeteneği istemciden gelir; bizim
boru hattımıza ikinci bir model EKLENMEZ ve determinizm bozulmaz.

Gelen her ifade çalıştırılmadan önce `sql_filtre.sql_guvenli_mi()`
doğrulayıcısından geçer. Reddedilirse gerekçesiyle döner.

=============================================================================
KURULUM (Claude Desktop)
=============================================================================
claude_desktop_config.json:

    {
      "mcpServers": {
        "ekap-ihale": {
          "command": "python",
          "args": ["C:/.../EkapUnified/scripts/mcp_sunucu.py"]
        }
      }
    }

`.env` dosyası projenin kökünden okunur — veri kaynağı (sqlite/postgres) ve
bağlantı bilgileri oradan gelir.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from mcp.server.mcpserver import MCPServer  # noqa: E402

from app.mcp import sorgular  # noqa: E402

sunucu = MCPServer(
    name="ekap-ihale",
    instructions=(
        "EKAP ihale veritabanına SALT OKUNUR erişim. İSBAK'ın 20 iş paketiyle "
        "ilgili ihaleleri bulmak için kullanılır.\n\n"
        "AKIŞ ÖNERİSİ: önce `aday_ihaleler` ile alan filtresinden geçen havuza bak; "
        "daralt gerekiyorsa `ihale_ara` ya da `serbest_sorgu` kullan; tek bir ihalenin "
        "ayrıntısı için `ihale_getir`.\n\n"
        "ÖNEMLİ: bu araçlar KARAR ÜRETMEZ. Filtreyi geçmek 'İSBAK'a uygun' demek "
        "değildir — sadece aday havuzudur. Gerekçeli karar için kullanıcıyı "
        "`scripts/analyze_tender.py <İKN>` komutuna yönlendir.\n\n"
        "`serbest_sorgu` kullanacaksan önce `sema_bilgisi` ile şemayı al; yalnızca "
        "WHERE gövdesi üret, veri değiştiren hiçbir komut yazma."
    ),
)


def _hata_sarmala(fn):
    """SorguHatasi'nı istemciye okunur metin olarak döndürür."""

    def sarmal(*a, **k):
        try:
            return fn(*a, **k)
        except sorgular.SorguHatasi as e:
            return {"hata": str(e)}

    sarmal.__name__ = fn.__name__
    sarmal.__doc__ = fn.__doc__
    return sarmal


@sunucu.tool(description="İhale adında metin arar; il, tür ve aktiflik ile daraltır.")
def ihale_ara(
    terim: str | None = None,
    il: str | None = None,
    ihale_turu: str | None = None,
    sadece_aktif: bool = True,
    limit: int = 20,
) -> dict:
    return _hata_sarmala(sorgular.ihale_ara)(terim, il, ihale_turu, sadece_aktif, limit)


@sunucu.tool(
    description="Tek ihalenin künyesi, OKAS kodları ve karar modeline giden "
                "TEMİZLENMİŞ ilan metni. 'model ne gördü' sorusunun cevabı."
)
def ihale_getir(ikn: str, tam_metin: bool = False) -> dict:
    return _hata_sarmala(sorgular.ihale_getir)(ikn, tam_metin)


@sunucu.tool(
    description="SQL alan filtresini geçen 'incelemeye değer' ihaleler. "
                "sadece_aktif=False ile tüm tablo (~50k, geçmiş dahil). "
                "DİKKAT: bu bir karar değil, aday havuzudur."
)
def aday_ihaleler(limit: int = 20, sadece_aktif: bool = True, genislik: str = "genis") -> dict:
    return _hata_sarmala(sorgular.aday_ihaleler)(limit, sadece_aktif, genislik)


@sunucu.tool(description="OKAS kataloğunda ada göre arama — kodun ne anlama geldiğini bulur.")
def okas_ara(terim: str, limit: int = 20) -> dict:
    return _hata_sarmala(sorgular.okas_ara)(terim, limit)


@sunucu.tool(description="İSBAK'ın 20 iş paketi: kod, ad, güçlü ve negatif terimler, OKAS ön ekleri.")
def profil_listesi() -> dict:
    return _hata_sarmala(sorgular.profil_listesi)()


@sunucu.tool(
    description="Daha önce taranmış ihalelerin kararları, gerekçeleri ve varsa insan etiketi."
)
def karar_gecmisi(ikn: str | None = None, limit: int = 20) -> dict:
    return _hata_sarmala(sorgular.karar_gecmisi)(ikn, limit)


@sunucu.tool(
    description="Doğrulanmış bir SQL WHERE ifadesiyle ihale arar. Önce `sema_bilgisi` "
                "aracıyla şemayı alın. Yalnızca WHERE gövdesi; veri değiştiren komut yasak."
)
def serbest_sorgu(where_ifadesi: str, limit: int = 20) -> dict:
    return _hata_sarmala(sorgular.serbest_sorgu)(where_ifadesi, limit)


@sunucu.tool(description="serbest_sorgu için tablo/sütun şeması ve yazım kuralları.")
def sema_bilgisi() -> dict:
    return sorgular.sema_bilgisi()


if __name__ == "__main__":
    sunucu.run()
