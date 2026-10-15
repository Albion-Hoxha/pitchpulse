# Lista e kontrollit për dorëzimin – PitchPulse

Afati zyrtar: **e mërkurë, 28 tetor 2026, 07:59** (ora e Kosovës). Synimi ynë: **e martë, 27 tetor, deri në 21:00**.
Innovation Studio: hyjmë **vetëm me linkun nga email-i i ftesës**.

---

## 24 tetor – materialet gati

- [ ] README, diagrami (`docs/architecture.png`) dhe skenari (`docs/video-script.md`) janë në GitHub
- [ ] Dizajni përfundimtar i Afronit është në GitHub dhe live
- [ ] Screenshot-et: `docs/screenshot-live.png` (desktop) dhe `docs/screenshot-mobile.png`
- [ ] Prova e plotë në linkun live: ndeshje me zë + producer + Ask the Match + evidence, në desktop dhe telefon

## 25–26 tetor – videoja

- [ ] Regjistrimi sipas skenarit, **nën 2 minuta** (gjyqtarët nuk shohin më shumë)
- [ ] Emri i plotë në fillim dhe në fund: **PitchPulse – AI Broadcast Director**
- [ ] Pa muzikë me të drejta autori, pa logo të Premier League apo klubeve të vërteta
- [ ] Momentet që duhet të shihen: gol live me **zë në shqip** · klik te **✓ fact-checked** · **Producer mode** · **Ask the Match** me "Microsoft Foundry agent" · **Foundry Portal** · **GitHub Actions** · diagrami
- [ ] Ngarkimi në **YouTube** si **Public** ose **Unlisted** (jo Private!) dhe kontrolli i linkut në një dritare InPrivate

## 27 tetor – dita e dorëzimit

**Mëngjes**
- [ ] Aplikacioni gjithmonë ndezur për gjyqtarët (që të mos presin 20 sekonda):
  ```bash
  az containerapp update -n pitchpulse -g pitchpulse-rg --min-replicas 1
  ```
- [ ] Hapim linkun live dhe luajmë një ndeshje të plotë – gjithçka funksionon?
- [ ] Nuk bëjmë më ndryshime në kod pas kësaj (vetëm rregullime urgjente)

**Pasdite – GitHub**
- [ ] Repo **Public**: github.com/Albion-Hoxha/pitchpulse → **Settings → General → Danger Zone → Change visibility → Public**
- [ ] Kontrollojmë në InPrivate që repo hapet pa kyçje dhe README duket mirë
- [ ] Kontrollojmë që `.env` **nuk** është në repo (vetëm `.env.example`)

**Mbrëmje – Innovation Studio** (me linkun e email-it)
- [ ] **Title:** PitchPulse – AI Broadcast Director
- [ ] **Tagline** dhe **Description** (nga `docs/submission.md`) – të përditësuara
- [ ] **Images/Video:** kopertina e para, screenshot-i i dyti, **linku i videos në YouTube**
- [ ] **Code repository:** https://github.com/Albion-Hoxha/pitchpulse
- [ ] **Team:** Albion (Owner), Afron, Rinesa – të tre te Team Members
- [ ] Linku live dhe udhëzimet e testimit janë në përshkrim
- [ ] Nëse ka buton **Submit / Submit project** – e klikojmë dhe ruajmë një screenshot të konfirmimit
- [ ] Hapim faqen e projektit si vizitor dhe kontrollojmë që çdo link funksionon

## 27 tetor – 10 nëntor – vlerësimi

- [ ] Aplikacioni mbetet **gjithmonë ndezur** deri më **11 nëntor, 08:59** (fundi i vlerësimit në orën e Kosovës)
- [ ] Nuk bëjmë push në `main` gjatë vlerësimit, përveç rregullimeve urgjente
- [ ] Një herë në 2–3 ditë: hapim linkun live dhe shohim kredit-in (portal.azure.com → **Education**)

**Nëse aplikacioni nuk hapet:**
1. Shohim gjendjen:
   ```bash
   az containerapp revision list -n pitchpulse -g pitchpulse-rg -o table
   ```
2. E rinisim versionin aktiv:
   ```bash
   az containerapp revision restart -n pitchpulse -g pitchpulse-rg --revision <emri-nga-lista>
   ```
3. Nëse prapë nuk funksionon, kthehemi te imazhi i mëparshëm që funksiononte (tag-u = commit-i):
   ```bash
   az containerapp update -n pitchpulse -g pitchpulse-rg --image ca5a2117c33eacr.azurecr.io/pitchpulse:<commit-i-paraprak>
   ```

## Pas 11 nëntorit

- [ ] Kthejmë aplikacionin në 0 kopje kur nuk përdoret (kursim):
  ```bash
  az containerapp update -n pitchpulse -g pitchpulse-rg --min-replicas 0
  ```
- [ ] **20 nëntor**: shpallja e fituesve. Nëse fitojmë, formularët (përfshirë **W-8BEN**) duhen dorëzuar **brenda 10 ditëve pune**.
