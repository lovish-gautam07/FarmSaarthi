# FarmSaarthi

FarmSaarthi is a vernacular crop-advice web app for Indian farmers. A farmer picks a language, state and district, and optionally types or speaks the crop they are interested in. They get:

- **"What to do now" cards**: short, colour-coded actions such as "Irrigate this week" or "Don't spray now" (rain is coming)
- the top 3 crops to grow now, with reasons
- all advice **read aloud** in their language
- **today's mandi prices** for the suggested crops
- answers to their own farming questions from **Kisan Call Centre** experts
- irrigation and fertilizer advice, and weather, pest and disease alerts, under "More details"

They can also upload a photo of their crop, which is stored in **Azure cloud storage** and shown in a per-district gallery. The app can be installed on a phone and still shows the last advice without internet.

## Features

- **13 languages**: English, Hindi, Telugu, Tamil, Marathi, Kannada, Punjabi, Bengali, Gujarati, Malayalam, Assamese, Urdu (right-to-left) and Odia. Text that isn't hand-written, and all advice and alerts, is translated with **Azure Translator**.
- **Voice input** for the crop interest, using the browser's speech recognition in the chosen language. Local names work too: "गेहूं", "bajra", "arhar" and so on.
- **Live weather** for each of the 762 districts from Open-Meteo: the last 3 months of rain, the 16-day forecast, temperature and humidity.
- **Season-aware recommendations**: a Random Forest model trained on a public 22-crop dataset plus wheat, sugarcane and millets (25 crops), combined with soil, season and climate checks. Crops that can be sown in the current season (kharif, rabi or zaid) are ranked first.
- **Crop photo upload to Azure**:
  - The photo goes to **Azure Blob Storage**, in a private container.
  - A record of each upload (state, district, crop, note, time) goes to **Azure Table Storage**.
  - Photos are checked before upload: JPG, PNG or WEBP only, 5 MB at most, and the file must be a real image. They are resized and stripped of all metadata, including GPS location.
  - The gallery shows photos through read-only links that expire after 30 minutes.
- **Action cards**, sorted red (urgent), amber, green, then neutral. They are built from the forecast:
  - "Heavy rain on Saturday" when a day reaches IMD's heavy-rain level (64.5 mm)
  - "Don't spray now" if rain is due within 3 days
  - "Irrigate this week" or "Skip irrigation this week"
  - heat, dry-spell, pest and fungal-disease warnings
  - a fertilizer tip
- **Read aloud** with **Azure AI Speech** (free F0 tier). A speaker button on every card and crop, plus "Read all aloud".
  - Audio for each sentence is made once and kept in Blob Storage, so replays don't use the free quota.
  - The server only reads text it wrote itself (each piece is HMAC-signed), so nobody can spend the quota on other text.
  - Without a key, the phone's built-in voice is used.
- **Regional crop rules**: crops grown only in some regions (coffee, apple, coconut, jute and others) are only suggested in the states that grow them. If a farmer asks about a crop that is out of season or out of region, they get a note explaining why, for example "Cotton is usually sown in kharif (June–October)".
- **"Was this advice useful?"** buttons with an optional comment.
  - Answers and anonymous usage records (state, district, language, suggested crop) go to Azure Table Storage.
  - **`/stats`** shows totals for evaluation: useful %, languages, states, crops, and analyses per day.
- **Installable and offline-ready**:
  - Works as a progressive web app (manifest and service worker) with an "Install app" button on Android.
  - The page opens without internet and shows the last saved advice with an "offline" banner.
- **Soil Health Card entry** (optional): soil type, N, P, K (kg/ha), pH and organic carbon.
  - Card ratings (low/medium/high) replace the state estimates in the model.
  - Fertilizer tips quote the farmer's own values.
  - The values are remembered on the phone.
- **Mandi prices**: today's Agmarknet prices for each suggested crop, from the free data.gov.in API. Shows the farmer's own district first, then the best price, plus the state's middle price.
- **Ask a farming question**: typed or spoken, in any of the 13 languages.
  - FarmSaarthi searches past questions that Kisan Call Centre experts answered in the farmer's state (48 million records on data.gov.in). It ranks them by text similarity (TF-IDF, no paid AI) and translates the answers with Azure Translator.
  - Each answer has read-aloud and a note to confirm chemical doses with the local KVK.
- **Weather alerts as free browser notifications** (Web Push):
  - The farmer taps "Get weather alerts" for a district.
  - A free GitHub Actions schedule asks the app once a day to check every subscribed district.
  - Notifications are sent only for heavy rain, heat, dry spells or rain before spraying, never the same alert twice.
- **Government schemes page** (`/schemes`): PM-KISAN, crop insurance, Kisan Credit Card, Soil Health Card, e-NAM, PM-KUSUM and the Kisan Call Centre number. Each has a plain-language summary, official links, tap-to-call and read-aloud.
- **Honest labels**: the page shows whether weather is live or estimated. Soil and market index values are marked as estimates unless they come from the farmer's Soil Health Card.

## How it fits together

```
Browser (templates/index.html + service worker templates/sw.js)
   │  /analyze  /speak  /feedback  /photos  /i18n/<lang>  /stats
   ▼
Flask app (app.py)
   ├── engine.py        crop model, action cards ──► weather.py ──► Open-Meteo API
   ├── translations.py  interface text           ──► Azure Translator
   ├── speech.py        read aloud               ──► Azure AI Speech (+ audio kept in Blob Storage)
   ├── events.py        usage + feedback         ──► Azure Table Storage (AppEvents)
   ├── datagov.py       mandi prices, KCC answers ──► data.gov.in API (Agmarknet, Kisan Call Centre)
   ├── alerts.py        weather notifications    ──► Web Push + Azure Table Storage (AlertSubscriptions)
   └── photos.py        image checks             ──► Azure Blob Storage (photo files)
                                                 ──► Azure Table Storage (photo records)
```

## Project structure

| Path | What it is |
| --- | --- |
| `app.py` | Flask app: page, analysis, photo upload and gallery, language text, health check |
| `engine.py` | Training, crop scoring, irrigation and fertilizer advice, alerts |
| `weather.py` | Open-Meteo client with a 3-hour cache |
| `translations.py` | Hand-written interface text plus Azure Translator, with English fallback |
| `photos.py` | Photo checks and clean-up, and Azure Blob + Table Storage |
| `speech.py` | Azure AI Speech text-to-speech, signed text, audio cache |
| `events.py` | Usage and feedback records, and the `/stats` numbers |
| `datagov.py` | data.gov.in client: mandi prices and Kisan Call Centre search |
| `alerts.py` | Weather-alert subscriptions and sending (Web Push) |
| `data/market_names.json` | How each crop and state is named in Agmarknet and Kisan Call Centre data |
| `data/schemes.json` | Text and official links for the schemes page |
| `templates/schemes.html` | The `/schemes` page |
| `scripts/check_market_names.py` | Checks the crop names against the live API (needs your key) |
| `scripts/make_vapid_keys.py` | Makes the key pair for weather-alert notifications |
| `templates/sw.js` | Service worker: offline copy of the page and styles |
| `templates/stats.html` | The `/stats` evaluation page |
| `static/icons/` | App icons for installing on a phone |
| `data/crop_recommendation.csv` | Public 22-crop dataset (see Data sources) |
| `data/farm_data.json` | Hand-written growing ranges, used to generate wheat, sugarcane and millets rows |
| `data/crop_meta.json` | Soil types, sowing seasons and local names for each crop |
| `data/india_districts.json` | 762 districts with coordinates |
| `data/i18n.json` | All interface text, per language |
| `scripts/geocode_districts.py` | One-time script that added district coordinates |
| `tests/` | Automatic tests (pytest) |
| `.github/workflows/` | Tests on every push; deployment to Azure App Service from `main` |
| `AZURE_SETUP.md` | Step-by-step Azure setup (student account, storage, translator, hosting) |

## Run it locally

You need Python 3.12 and Node.js (Node runs the Azure Storage emulator).

```bash
python3.12 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements-dev.txt
cp .env.example .env               # Windows: copy .env.example .env
```

Start the Azure Storage emulator (Azurite) in a second terminal. It stands in for Azure Blob and Table Storage, so you don't need an Azure account to develop:

```bash
npx azurite --silent --skipApiVersionCheck --location .azurite
```

Then start the app:

```bash
python app.py
```

Open http://127.0.0.1:8000.

`.env.example` already points at the emulator. To use your real Azure storage account, put its connection string in `.env` instead. To translate advice, add your Translator key. See [AZURE_SETUP.md](AZURE_SETUP.md).

## Settings

| Variable | Needed for | Default |
| --- | --- | --- |
| `AZURE_STORAGE_CONNECTION_STRING` | Photo upload and gallery (off if empty) | empty |
| `AZURE_STORAGE_CONTAINER` | Blob container for photos | `crop-photos` |
| `AZURE_STORAGE_TABLE` | Table for photo records | `CropPhotos` |
| `AZURE_TRANSLATOR_KEY` | Translating advice and missing interface text (English if empty) | empty |
| `AZURE_TRANSLATOR_REGION` | Region of the Translator resource | empty |
| `AZURE_SPEECH_KEY` | Read-aloud with Azure voices (phone voice if empty) | empty |
| `AZURE_SPEECH_REGION` | Region of the Speech resource | empty |
| `AZURE_STORAGE_EVENTS_TABLE` | Table for usage and feedback records | `AppEvents` |
| `SECRET_KEY` | Signs read-aloud text; set it so links survive restarts | random per start |
| `DATA_GOV_IN_API_KEY` | Mandi prices and question answers (both off if empty) | empty |
| `KCC_FETCH_LIMIT` | How many recent Kisan Call Centre records to search per question | `200` |
| `VAPID_PUBLIC_KEY`, `VAPID_PRIVATE_KEY`, `VAPID_SUBJECT` | Weather-alert notifications (off if empty) | empty |
| `AZURE_STORAGE_ALERTS_TABLE` | Table for alert subscriptions | `AlertSubscriptions` |
| `TASK_KEY` | Secret the daily alert job must send | empty (job disabled) |
| `PORT` | Port for `python app.py` | `8000` |

Never commit `.env` or real keys. `.gitignore` already excludes `.env`.

## Tests

```bash
pytest -q
```

To also run the real Blob and Table Storage round trip, start Azurite first and run:

```bash
FARMSAARTHI_TEST_AZURITE=1 pytest -q
```

GitHub Actions runs the full suite, including the emulator round trip, on every push and pull request.

## Endpoints

| Method and path | Purpose |
| --- | --- |
| `GET /?language=hi` | The app page |
| `POST /analyze` | JSON `{language, state, district, crop_interest}` returns crops, advice, alerts and model info |
| `GET /i18n/<language>` | Interface text for one language |
| `POST /photos` | Multipart upload: `photo`, `state`, `district`, optional `crop`, `note`, `language` (10 uploads per 10 minutes per address) |
| `GET /photos?state=&district=&limit=` | Recent photos, newest first, with expiring read links |
| `POST /speak` | JSON `{text, sig, language}` (text and signature come from `/analyze`) returns MP3 audio |
| `POST /feedback` | JSON `{useful, state, district, language, analysis_id, comment?, feedback_id?}` |
| `GET /stats`, `GET /stats.json` | Usage and feedback totals |
| `GET /prices?state=&district=&crops=wheat,maize` | Today's mandi prices for up to 4 crops |
| `POST /ask` | JSON `{question, state, district, crop?, language}` returns similar answered Kisan Call Centre questions |
| `POST /alerts/subscribe`, `POST /alerts/unsubscribe` | Turn weather alerts on or off for one browser |
| `POST /tasks/send-alerts` | Daily alert run; needs header `X-Task-Key` |
| `GET /schemes?language=hi` | Government schemes page |
| `GET /sw.js`, `GET /manifest.webmanifest` | Install and offline support |
| `GET /healthz` | Shows which Azure services are configured |

## Data sources and limits

- **Crop dataset**: `data/crop_recommendation.csv` is the widely used Kaggle "Crop Recommendation Dataset" (2,200 rows, 22 crops). This copy is from the GitHub repository `yogeshhk/TeachingDataScience` (MIT licence). Its original collection method isn't documented, and its value ranges per crop are narrow. So the model's ~99% accuracy only measures held-out rows of this data, not real fields.
- **Wheat, sugarcane and millets** are missing from that dataset. 100 rows each are generated from the hand-written ranges in `data/farm_data.json`.
- **Weather**: [Open-Meteo](https://open-meteo.com/), CC BY 4.0.
- **District coordinates**: Open-Meteo geocoding (GeoNames, CC BY 4.0) and OpenStreetMap Nominatim (© OpenStreetMap contributors, ODbL). One district, Shahid Bhagat Singh Nagar, has no match and uses the average of Punjab's districts.
- **Soil N/P/K, pH, soil type and the market index** are state-level estimates, not measurements, unless the farmer enters Soil Health Card values. The app labels which is which.
- **Mandi prices and Kisan Call Centre answers**: [data.gov.in](https://data.gov.in), Government Open Data License – India. Prices are today's reported arrivals (₹ per quintal) and can be missing for a crop or state on a given day. Answers are past advice given to other farmers, so the page asks farmers to confirm chemical doses with their KVK.
- **Crop names in those datasets** (`data/market_names.json`) were checked against the live API on 19 November 2026. Every Kisan Call Centre name has records, and every mandi name had prices that day except arhar and rajma. Run `scripts/check_market_names.py` to check again.
- **data.gov.in ignores requests that use Python's default User-Agent**, so the client sends `FarmSaarthi/1.0` (see `datagov.py`).
- **Scheme facts** (amounts, premium limits, the 72-hour loss-reporting rule) were checked against PIB releases in November 2026. They can change, so the page links to each official site.
