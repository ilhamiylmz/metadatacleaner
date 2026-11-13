# 🖼️ Image Metadata & AI Tag Inspector / Editor / Stripper

Görsellerin içerdiği **tüm üst verileri (metadata)**, **Yapay Zeka (AI) üretim izlerini**, **C2PA / JUMBF Content Credentials** manifestolarını, **Google SynthID** filigran bildirimlerini ve **sosyal medya takip etiketlerini (Facebook/Instagram IPTC `FBMD`)** analiz eden, düzenleyen ve **görüntü kalitesini bozmadan (lossless)** temizleyen kapsamlı bir Python uygulamasıdır.

Hem **Terminal (CLI & İnteraktif Menü)** üzerinden hem de **Masaüstü Arayüzü (GTK 3 GUI)** veya **Web Tarayıcı Arayüzü (Web GUI)** üzerinden çalıştırılabilir.

---

## Temel Özellikler

1. ** Yapay Zeka (AI) ve İçerik Kökeni (Provenance) Tespiti**
   - **C2PA / JUMBF (`JPEG APP11 0xFFEB`, `PNG caBX`, `WebP C2PA`):**
     - ISO/IEC 19566-5 JUMBF kutu hiyerarşisini ve CBOR kodlu manifestoları (`c2pa.claim.v2`, `c2pa.actions.v2`, `c2pa.ingredient.v3`, `c2pa.hash.data`) tamamen çözümler.
     - Üretici bilgisini (`Google C2PA Core Generator Library` vb.), yapılan işlemleri (`c2pa.created`: *"Created by Google Generative AI."*, `c2pa.edited`: *"Applied imperceptible SynthID watermark."* vb.) ve IPTC dijital kaynak tiplerini (`trainedAlgorithmicMedia`) gösterir.
     - **Kriptografik İmza (`c2pa.signature` COSE_Sign1):** X.509 sertifika zincirini (imzalayan kurum, kök/ara sertifika, geçerlilik tarihleri, seri numarası) ve zaman damgası otoritesi (TSA) verilerini ayrıştırır.
   - **Diğer AI Üretim Etiketleri:**
     - **Stable Diffusion (A1111 / Forge):** Pozitif/negatif prompt, `Steps`, `Sampler`, `CFG scale`, `Seed`, `Model` parametrelerini ayrıştırır.
     - **ComfyUI:** PNG `prompt` ve `workflow` JSON düğümlerini tespit eder.
     - **Midjourney, DALL-E, Adobe Firefly, NovelAI:** EXIF, XMP ve yorum alanlarındaki AI izlerini yakalar.

2. ** Sosyal Medya ve Standart Metadata Analizi**
   - **IPTC / Photoshop 8BIM (`APP13 0xFFED`):** Başlık (`ObjectName`), Yazar (`Byline`), Anahtar Kelimeler (`Keywords`), Telif (`CopyrightNotice`) ve özellikle Facebook/Instagram'ın görsellere eklediği `SpecialInstructions` (`FBMD23000...`) takip kodlarını tespit eder.
   - **EXIF (`APP1 0xFFE1` / PNG `eXIf`):** `IFD0`, `ExifIFD`, `GPSIFD` ve `IFD1` tablolarındaki tüm kamera, yazılım, tarih, yazar ve konum verilerini okur.
   - **XMP (`APP1` RDF/XML & PNG `iTXt`):** Dublin Core (`dc:title`, `dc:creator`, `dc:description`, `dc:subject`, `dc:rights`), `xmp:CreatorTool`, `photoshop:Credit` ve `Iptc4xmpExt:DigitalSourceType` alanlarını çözümler.
   - **Teknik & Binary Segment Analizi:** Dosya boyutu, `SHA-256` özeti, çözünürlük, bit derinliği, renk kanalları, chroma subsampling (`4:2:0`, `4:4:4`), sıkıştırma modu (`SOF0 Baseline DCT` / `SOF2 Progressive DCT`) ve tüm binary segment haritasını (`SOI`, `APP0`, `APP11`, `APP13`, `DQT`, `DHT`, `SOS`, `EOI`) listeler.

3. ** Kayıpsız (Lossless) Metadata Silme (Strip)**
   - Görsel piksellerini **yeniden sıkıştırmadan (re-encoding yapmadan)** doğrudan binary segment düzeyinde (`APP1`, `APP11`, `APP13`, `COM`, `eXIf`, `caBX`, `tEXt`, `iTXt`) temizlik yapar.
   - Görüntü kalitesinde **%0 kayıp** ile tüm AI etiketlerini, C2PA sertifikalarını, EXIF/IPTC/XMP verilerini kaldırır.
   - Tek bir görseli veya `process/` klasöründeki tüm görselleri tek komutla temizleyebilir; isteğe bağlı `.bak` yedeği alabilir veya sadece seçili kategorileri (ör. yalnızca C2PA/AI etiketlerini) silebilir.

4. ** Mevcut Metadata Alanlarını Düzenleme (Edit)**
   - Görseldeki mevcut **EXIF**, **IPTC**, **XMP**, **JPEG/PNG Yorum (Comment)** ve **C2PA** alanlarını görüntüleyip değiştirmenize, yeni etiket eklemenize veya belirli bir etiketi silmenize olanak tanır.

---

##  Proje Yapısı

```text
metadata/
├── app.py              # Terminal (CLI) uygulaması, zengin tablo/ağaç çıktıları ve interaktif menü
├── gui.py              # Masaüstü (GTK 3.0) GUI ve yerleşik Web Tarayıcı GUI arayüzü
├── metadata_engine.py  # Saf Python (Pure-Python) JUMBF/CBOR/C2PA, EXIF, IPTC, XMP okuma/yazma/silme motoru
├── README.md           # Proje dokümantasyonu
└── process/            # İşlenecek ve analiz edilecek görsellerin bulunduğu varsayılan klasör
```

---

##  Kurulum ve Gereksinimler

Proje, harici bir CLI aracına (`exiftool` vb.) ihtiyaç duymadan **Saf Python (Python 3.10+)** ile çalışacak şekilde tasarlanmıştır.

- **Zorunlu:** Python 3.10+ (Standart kütüphanelerle tam fonksiyonel çalışır)
- **Zengin Terminal Çıktısı ve X.509 Sertifika Çözümleme İçin (Sistemde Kurulu):**
  - `rich` (renkli tablolar, paneller ve C2PA manifesto ağacı için)
  - `cryptography` (C2PA COSE_Sign1 içindeki DER X.509 sertifikalarını çözümlemek için)
- **Masaüstü GUI İçin (Sistemde Kurulu):**
  - `PyGObject` (`gi.repository.Gtk` 3.0 ve `GdkPixbuf` 2.0)
  - *Not:* Grafik ekran (Wayland/X11) bulunmayan bir ortamda çalıştırılırsa uygulama otomatik olarak yerleşik **Web GUI** (`http://127.0.0.1:8765`) moduna geçer.

---

##  Kullanım Kılavuzu

### 1. Terminalde Hızlı Analiz ve İnteraktif Menü

Hiçbir parametre vermeden çalıştırdığınızda `process/` klasöründeki tüm görsellerin özet tablosunu ve detaylı metadata/AI raporlarını ekrana basar, ardından interaktif menüyü açmayı teklif eder:

```bash
python3 app.py
```

Doğrudan **İnteraktif Terminal Menüsünü** açmak için:

```bash
python3 app.py menu
# veya
python3 app.py --cli
```

**İnteraktif Menü Seçenekleri:**
- `[1]` `process` klasöründeki tüm görsellerin tam metadata ve AI/C2PA raporunu göster
- `[2]` Tek bir görseli seçip detaylı incele
- `[3]` Bir görselin mevcut metadata verilerini interaktif olarak düzenle (Edit)
- `[4]` Tek bir görselin tüm metadata verilerini sil (Strip)
- `[5]` `process` klasöründeki **tüm** görsellerin metadata verilerini topluca sil
- `[6]` Masaüstü / Web GUI arayüzünü başlat
- `[0]` Çıkış

---

### 2. Grafik Arayüz (GUI) ile Kullanım

#### Masaüstü Arayüzü (GTK 3.0)
```bash
python3 app.py --gui
# veya doğrudan:
python3 gui.py
```

#### Web Tarayıcı Arayüzü (Web GUI)
Tarayıcı üzerinden modern karanlık tema arayüzüyle kullanmak isterseniz:
```bash
python3 app.py --web
```
Bu komut yerel sunucuyu `http://127.0.0.1:8765` adresinde başlatır ve varsayılan tarayıcınızda açar.

**GUI Özellikleri:**
- Sol panelde `process/` klasöründeki görselleri boyutları ve AI/Metadata durum rozetleriyle (`AI / C2PA`, `IPTC / FBMD`, `Clean`) listeler.
- Seçilen görselin önizlemesini ve renk kodlu **AI / Metadata Karar (Verdict)** bandını gösterir.
- **4 Detay Sekmesi:**
  1. `Overview & AI Tags`: Dosya özeti, AI/SynthID tespiti ve dolu olan tüm metadata alanları.
  2. `C2PA / AI Provenance`: Tüm JUMBF/C2PA manifesto zinciri, Google Generative AI / SynthID aksiyonları, doğrulama kodları ve X.509 sertifika detayları.
  3. `EXIF / IPTC / XMP / Comments`: Tüm standart metadata tabloları ve ham XMP XML içeriği.
  4. `Binary Segments & Hashes`: `SHA-256` özeti ve JPEG/PNG binary segmentlerinin byte ofset/uzunluk haritası.
- **Edit Metadata... Butonu:** Mevcut `IPTC`, `EXIF`, `XMP`, `Comment` ve `C2PA` alanlarını sekmeli bir formda düzenleyip kaydetmenizi sağlar.
- **Remove All Metadata... Butonu:** Hangi metadata kategorilerinin (`C2PA`, `IPTC`, `EXIF`, `XMP`, `Comments`) silineceğini seçerek veya tamamını işaretleyerek kayıpsız temizlik yapar.

---

### 3. Komut Satırı (CLI) Alt Komutları

####  Metadata Görüntüleme (`show`)
```bash
# process/ klasöründeki tüm görselleri analiz et ve çık
python3 app.py show

# Tek bir görselin tüm metadata detaylarını göster
python3 app.py show process/a1-wallpaper-f0rf0xsakeffs.jpg

# Çıktıyı JSON formatında al (otomasyon / scriptler için)
python3 app.py show process/a1-wallpaper-f0rf0xsakeffs.jpg --json
```

####  Metadata Silme / Temizleme (`strip`)
```bash
# Tek bir görseldeki tüm metadata ve AI etiketlerini sil (üzerine yazar)
python3 app.py strip process/a1-wallpaper-f0rf0xsakeffs.jpg

# Silmeden önce .bak uzantılı yedek dosya oluştur
python3 app.py strip process/a1-wallpaper-f0rf0xsakeffs.jpg --backup

# Orijinal dosyaya dokunmadan temizlenmiş görseli farklı bir dosyaya kaydet
python3 app.py strip process/a1-wallpaper-f0rf0xsakeffs.jpg -o process/a1_clean.jpg

# Sadece C2PA / AI Content Credentials segmentini kaldır (EXIF/IPTC/XMP kalsın)
python3 app.py strip process/a1-wallpaper-f0rf0xsakeffs.jpg --c2pa-only

# process/ klasöründeki TÜM görsellerin metadata verilerini tek seferde temizle
python3 app.py strip all
```

####  Metadata Düzenleme (`edit`)
```bash
# Bir görsel için interaktif terminal düzenleyicisini başlat
python3 app.py edit process/a1-wallpaper-f0rf0xsakeffs.jpg

# Komut satırı parametreleriyle doğrudan etiket değiştir / ekle / sil:
python3 app.py edit process/857340270_18344740396256249_8853371540543943019_n.jpg \
  --clear iptc.SpecialInstructions \
  --set exif.Artist="Theresa" \
  --set exif.Copyright="2026 Theresa" \
  --set iptc.ObjectName="Manzara Fotoğrafı" \
  --set iptc.Keywords="doğa, manzara, arşiv" \
  --set xmp.dc:title="Manzara Fotoğrafı" \
  --set comment.Comment="Düzenlenmiş görsel"

# C2PA içindeki AI açıklamasını değiştir veya --remove-c2pa ile C2PA'yı tamamen kaldır:
python3 app.py edit process/a1-wallpaper-f0rf0xsakeffs.jpg \
  --set "c2pa.Manifest[0].Action[0].description=Custom Description"
```

---

##  Düzenlenebilir Metadata Alanları Referansı

`edit` komutunda `--set KATEGORİ.ALAN="Değer"` veya `--clear KATEGORİ.ALAN` şeklinde kullanabileceğiniz başlıca alanlar:

| Kategori | Alan Adı (Key) | Açıklama |
| :--- | :--- | :--- |
| **`exif`** | `ImageDescription` | Görsel açıklaması / başlığı |
| **`exif`** | `Artist` | Fotoğrafçı / Oluşturan kişi |
| **`exif`** | `Copyright` | Telif hakkı metni |
| **`exif`** | `Software` | Oluşturan / işleyen yazılım adı |
| **`exif`** | `Make` / `Model` | Kamera üreticisi / modeli |
| **`exif`** | `DateTime` / `DateTimeOriginal` | Tarih ve saat (`YYYY:MM:DD HH:MM:SS`) |
| **`exif`** | `UserComment` | Kullanıcı yorumu (ASCII / Unicode destekli) |
| **`iptc`** | `ObjectName` | IPTC Başlık (`2:05`) |
| **`iptc`** | `Headline` | Özet başlık (`2:105`) |
| **`iptc`** | `CaptionAbstract` | Alt yazı / detaylı açıklama (`2:120`) |
| **`iptc`** | `Byline` | Yazar / Fotoğrafçı (`2:80`) |
| **`iptc`** | `Credit` / `Source` | Kredi (`2:110`) / Kaynak (`2:115`) |
| **`iptc`** | `CopyrightNotice` | IPTC Telif bildirimi (`2:116`) |
| **`iptc`** | `Keywords` | Virgülle ayrılmış anahtar kelimeler (`2:25`) |
| **`iptc`** | `SpecialInstructions` | Özel talimatlar / Sosyal medya `FBMD` kodu (`2:40`) |
| **`iptc`** | `DigitalSourceType` | IPTC Dijital kaynak tipi (`2:135`) |
| **`xmp`** | `dc:title` / `dc:description` | XMP Dublin Core başlık ve açıklama |
| **`xmp`** | `dc:creator` / `dc:rights` | XMP Oluşturan ve telif hakları |
| **`xmp`** | `dc:subject` | XMP Konu / etiket listesi |
| **`xmp`** | `xmp:CreatorTool` | XMP Oluşturucu araç bilgisi |
| **`comment`** | `Comment` | JPEG `COM` (`0xFFFE`) veya PNG `tEXt` yorum alanı |
| **`c2pa`** | `Manifest[i].GeneratorName` | C2PA manifesto üretici yazılım adı |
| **`c2pa`** | `Manifest[i].Action[j].description` | C2PA eylem açıklaması (ör. Google Generative AI / SynthID) |
| **`c2pa`** | `Manifest[i].Action[j].digitalSourceType` | C2PA eylem dijital kaynak URI'si |

---

##  Python Modülü Olarak Kullanım (`metadata_engine.py`)

Kendi Python scriptlerinizde `metadata_engine` modülünü doğrudan içe aktararak kullanabilirsiniz:

```python
from metadata_engine import analyze_image, strip_all_metadata, update_image_metadata

# 1. Görselin tüm metadata ve AI etiketlerini analiz et
report = analyze_image("process/a1-wallpaper-f0rf0xsakeffs.jpg")
print("Karar:", report["ai_summary"]["verdict"])
print("Rozetler:", report["ai_summary"]["badges"])
print("C2PA Aksiyonları:", report["c2pa"]["all_actions"])

# 2. Metadata verilerini düzenle
update_image_metadata(
    "process/a1-wallpaper-f0rf0xsakeffs.jpg",
    edits={
        "exif": {"Artist": "Theresa", "Software": "Metadata Studio"},
        "iptc": {"ObjectName": "Sci-Fi Wallpaper", "Keywords": "wallpaper, ai, art"},
    },
    output_path="process/a1_edited.jpg",
)

# 3. Tüm metadata verilerini kayıpsız (lossless) olarak temizle
result = strip_all_metadata(
    "process/a1-wallpaper-f0rf0xsakeffs.jpg",
    output_path="process/a1_clean.jpg",
)
print(f"Silinen byte: {result['bytes_saved']}, Silinen segmentler: {result['removed_segments']}")
```

