# Laptop Setup — Shirazi + Hadith Library Search

Is branch (`research-integration`) mein Shirazi assistant + hadith
library search aik hi project mein hai. Is guide se aap isay apne
laptop par chala sakte hain, phir Telegram / WhatsApp se connect
kar sakte hain.

## 1. Clone karein

```bash
git clone -b research-integration https://github.com/TalhaGoharWeb/shirazi.git
cd shirazi
```

## 2. Python + dependencies

- **Python 3.11 minimum** (3.13 tak tested).
- Dependencies install karein:

```bash
pip install -r requirements.txt
# ya: python setup.py   (Windows: py setup.py)
```

## 3. Gemini API key (free tier)

Pehli run par setup screen aayegi — apni Gemini API key wahan dein.
Key sirf aapki machine par `config/api_keys.json` mein rehti hai
(ye file gitignored hai — kabhi commit nahi hoti). Search khud
mukammal offline hai jab DBs local hon; internet sirf assistant
ke dimagh (Gemini) ke liye chahiye, search ke liye nahi.

## 4. Hadith parts Drive se download karein (zaroori — yehi mukammal project hai)

**Mukammal project ka matlab:** ye repo (code) + 8 FTS5 parts
(data) = **poori 20,299 kitabein**. GitHub par 111 GB rakhna
mumkin nahi (GitHub ki per-file limit 100 MB hai), is liye FTS5
files Drive folders mein hain — lekin wo isi project ka hissa
hain: neechay diye gaye 8 folders se parts download kar ke
`research/hadith-library/` mein rakhein, to laptop par project
mukammal ho jayega (8 parts, 20,299 kitabein, sab searchable).

**TXT is project ka hissa nahi — zero TXT files.** Search ko TXT
ki zaroorat nahi (FTS5 DB mein poora matn mojood hai), is liye
TXT na is repo mein hai, na download list mein — is se project
ka size bilawajah bara nahi hota aur download aasan rehta hai.

8 parts total **~111 GB** hain. Sab aik sath ya aik aik kar ke
download kar sakte hain — jo part local nahi hoga, plugin jawab
mein khud bata dega ke wo part Drive-only hai (khamoshi se
chhupaya nahi jayega).

Har part ke liye: us folder se saari files **aik folder** mein
download karein, phir neechay di gayi command se jorein aur SHA
verify karein. Bani hui `.sqlite` file ko
`research/hadith-library/` mein rakhein (default jagah — koi env
var set karne ki zaroorat nahi).

**Parts 1–7** (Linux/Mac):

```bash
cat hadith_partN_fts5.sqlite.part* > hadith_partN_fts5.sqlite
sha256sum -c SHA256SUMS.txt
```

**Parts 1–7** (Windows cmd):

```cmd
copy /b part01+part02+...+partNN hadith_partN_fts5.sqlite
certutil -hashfile hadith_partN_fts5.sqlite SHA256
```

SHA ko neechay diye gaye whole-file SHA se milayein (poora SHA
`research/hadith-library/partN_SHA256SUMS.txt` mein bhi hai).

| Part | Categories | Folder | Files | Whole-file SHA-256 |
|------|-----------|--------|-------|--------------------|
| 1 | kutub_sitta + adhkar | https://drive.google.com/drive/folders/1v0U94AmpJlldT5hY_xz_3ZOEn-FLoPoi | 68 parts + SHA | `74c9326eebbe7e7d7362d2320a2bad04d6550d40413c9935909de4ededfd4e9e` |
| 2 | masanid_ukhra + raqaaiq_adab | https://drive.google.com/drive/folders/17mD1Jz25m6_ryg2-rct9q0RGobpzBTkf | 58 parts + SHA | `ecfc756b534409a2b92bae0a06ca06970dcbbf28f40892778d0a5ff20b61122e` |
| 3 | majmuat + masanid_aimma | https://drive.google.com/drive/folders/1YJE48tVVxqD43A6ZWvSbXjwo3ax_HFT_ | 58 parts + SHA | `c8816bec01b6dc6fffd742eeca4ad80a2f78097badfb5a94441dd5b8f6fb3b01` |
| 4 | tarajim + mustalah | https://drive.google.com/drive/folders/1IZ0nm8mFZqhmze5-QvpApzzbMpKxUOxY | 55 parts + SHA | `0350fd8d21914934a5c97e8a9329e900b524f3b891eb117fc45c0262275c3974` |
| 5 | jarh + fiqh_aam_ref + maktabat_bahth | https://drive.google.com/drive/folders/1lZpfn67DH1hWydGLerEOP7m0uAFjR94m | 54 parts + SHA | `281d0c91580fd31076e28b0f34c6557096f2b054c25408befc393ffcdc8ec0b8` |
| 6 | ulum_quran + fatawa_ref + mabahith_quran | https://drive.google.com/drive/folders/1vLciend_HNKvnjfHEi6RB2VyRcZ59ZLm | 55 parts + SHA | `8f861684c47e230064d19bb5e2797f6d90205e22b2eb513dff3217a40463a861` |
| 7 | sira + mutun + shuruh + adilla_faharis + ajza | https://drive.google.com/drive/folders/1L5CvZ9Jd8585Qew0kNQqh2H3W4I96iIN | 58 parts + SHA | `5c68a62a6e7d2f93fa9f25b52a4e28fffb4518acf46a96de414cc94ce134156e` |
| 8 | takhrij + faharis_hadith + gharib_maajim + ulum + buhuth_masail + ilal | https://drive.google.com/drive/folders/1mVZY_XI7hsywte5vFkqQuLp8GP0Hbhr0 | 19 chunk files + SHA | `0730a243258cdc12b499049ecfc845fda17508fc3aff2e2351967dbb0f4c7c1c` |

**Part 8 khaas note:** is ke chunks 02/03/04 upload ke waqt fail
hue thay aur 250 MiB sub-chunks mein bheje gaye thay. Is liye
is ki reassembly is tarteeb se karein:

```bash
cat hadith_part8_fts5.sqlite.part01of04 \
    hadith_part8_fts5.sqlite.part02of04.sub0[1-6]of06 \
    hadith_part8_fts5.sqlite.part03of04.sub0[1-6]of06 \
    hadith_part8_fts5.sqlite.part04of04.sub0[1-6]of06 \
    > hadith_part8_fts5.sqlite
sha256sum -c SHA256SUMS.txt
```

(Windows par `copy /b` mein sub-chunks isi tarteeb se likhein.)

**Note:** `search_all.py` ka `--fetch-missing` option sirf
sandbox mein chalta hai (us ko Muse ka Drive CLI chahiye) —
laptop par oopar di gayi manual download hi tareeqa hai.

TXT-home (38,285 TXT files, research ke liye backup) yahan hai,
lekin Shirazi chalane ke liye zaroori nahi:
https://drive.google.com/drive/folders/1eXPWwFDRiNn5tkmuQoG6nSF29rsVVSD9

## 5. Shirazi chalayein

```bash
python main.py   # Windows: py main.py
```

Plugin startup par khud load hota hai. Phir aam zaban mein
sawal karein (Urdu / Roman Urdu / Arabic) — misal: "Niyyat wali
hadith ka matn aur hawala batao". Gemini khud `hadith_search`
chala kar jawab dega: matn + kitab + musannif + safha + part.

## 6. Telegram / WhatsApp se connect (phase 2)

- **Telegram (remote control pehle se mojood):**
  `plugins/telegram_remote.py` pehle se hai. Tareeqa: @BotFather
  se bot banayein, token + apni numeric chat id Shirazi config
  mein `plugin_config.telegram_remote` mein set karein, phir
  `action=poll` se commands lein (/say, /launch, /kill, /lock,
  /brief, /photo — /launch aur /kill sirf allowlist par chalte
  hain). **Telegram par sawal-jawab (question → hadith jawab)
  is branch mein nahi hai** — us ke liye aik chhota /ask izafa
  chahiye hoga (agli phase).
- **WhatsApp (abhi koi plugin nahi):** sirf official WhatsApp
  Business API ka rasta hai (Meta app + phone number + webhook
  + naya plugin). Personal WhatsApp session ko kabhi commit ya
  share na karein — `config/whatsapp_web/` isi liye gitignored
  hai: usay commit karna apna WhatsApp account kisi aur ko dena
  hai. WhatsApp connection ke liye Meta setup aap ki taraf se
  chahiye hoga, phir plugin banaya jayega.

## 7. Venue aur jagah ka khulasa

- **Code:** GitHub (ye repo). **Data (FTS5 DBs):** sirf Google
  Drive — kabhi GitHub par nahi (size bhi ijazat nahi deta).
  **TXT: is project mein zero** — na repo mein, na download
  list mein (search ko TXT ki zaroorat nahi).
- **Disk:** 8 parts ke liye ~111 GB + app. Aik aik part bhi
  chal sakta hai (plugin missing parts khud batata hai).
- **Secrets:** kabhi commit nahi hote (`.gitignore` dekhein).
