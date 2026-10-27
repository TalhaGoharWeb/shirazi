# Shirazi Research Rules (binding)

Ye usool Shirazi ki har research — khaas kar hadith library search —
par laazim hain. In ke khilaaf koi jawab, tool ya plugin nahi chalega.

## 1. Fiqh rulings ka source

- Fiqh ka **ruling sirf muntakhab madhhab ki mustanad kitabon**
  se aayega. Koi cross-madhhab borrowing nahi.
- **Wahhabi / Salafi literature ruling source nahi** ban sakti.
- Mu'tamad aur mufta-bihi mein farq lazmi wazeh ho.
- **Quran aur hadith sirf dalil** (support) ke tor par aayenge —
  hadith ka matn milna kisi maslay ka fatwa nahi hai.

## 2. Har jawab mein hawala

- Har nateeje ke sath **kitab ka naam + musannif + safha +
  part file** aana lazmi hai. Kabhi sirf book_id ya be-hawala
  matn nahi.
- Jo part local nahi, usay **missing bataya jaye, khamoshi se
  chhupaya na jaye** — aadhi coverage ko poori coverage kehna
  ghalat hai.

## 3. Page numbers ka caveat (chhupaya na jaye)

- Page numbers **Aljam3 digitized-file pages** hain; printed
  edition ki pagination mukhtalif ho sakti hai. Ahem iqtibas
  (key quotation) ki tasdeeq hamesha original / PDF se hogi.

## 4. Hybrid retrieval

- Aljam3 primary retrieval hai — **madhhab / book filter lazmi**
  hai; generic search jo madhhabs ko mila de, istemal nahi hogi.
- Shamela citation-grade verification aur offline layer hai —
  usay poori tarah replace nahi kiya jayega.

## 5. Library ke baare mein sachai

- Hadith library mein 25 categories hain — in mein supplementary
  / reference categories bhi hain (masalan 52 fatawa_ref aur 58
  fiqh_aam_ref mixed / general fiqh reference hain). Ye
  **research reference** hain, ruling source nahi; is se madhhab
  policy par koi asar nahi parta.
- Maloom kami: kutub_sitta kitab 21150, file 44833, source par
  khaali hai (0 bytes) — us ka koi alternate edition substitute
  nahi kiya gaya (citation accuracy ke liye).

## 6. Venue — code kahan, data kahan

- **Code:** GitHub (ye repo). **DBs aur TXT:** sirf Google Drive.
  `.sqlite` aur TXT files kabhi GitHub par nahi jayengi.
- **Secrets** (API keys, tokens, WhatsApp session) kabhi commit
  nahi honge — `.gitignore` dekhein.

## 7. Free-first

- Search mukammal offline hai jab DBs local hon. Assistant ka
  dimagh free-tier APIs par chalta hai (Gemini free tier).
- Koi paid rasta assume nahi kiya jayega; paid option ho to us
  ki qeemat aur zaroorat pehle batayi jayegi, chhupayi nahi.
