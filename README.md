# FarmSaarthi

FarmSaarthi is a vernacular crop-advice web app for Indian farmers. A farmer picks a language, state and district, and optionally types or speaks the crop they are interested in. They get:

- the top 3 crops to sow now, with reasons
- irrigation and fertilizer advice
- weather, pest and disease alerts

They can also upload a photo of their crop, which is stored in **Azure cloud storage** and shown in a per-district gallery.

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
- **Honest labels**: the page shows whether weather is live or estimated. Soil and market values are marked as estimates.

## How it fits together

```
Browser (templates/index.html)
   │  /analyze  /photos  /i18n/<lang>
   ▼
Flask app (app.py)
   ├── engine.py        crop model + advice  ──► weather.py ──► Open-Meteo API
   ├── translations.py  interface text       ──► Azure Translator
   └── photos.py        image checks         ──► Azure Blob Storage (photo files)
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
| `GET /healthz` | Shows whether photo storage and the translator are configured |

## Data sources and limits

- **Crop dataset**: `data/crop_recommendation.csv` is the widely used Kaggle "Crop Recommendation Dataset" (2,200 rows, 22 crops). This copy is from the GitHub repository `yogeshhk/TeachingDataScience` (MIT licence). Its original collection method isn't documented, and its value ranges per crop are narrow. So the model's ~99% accuracy only measures held-out rows of this data, not real fields.
- **Wheat, sugarcane and millets** are missing from that dataset. 100 rows each are generated from the hand-written ranges in `data/farm_data.json`.
- **Weather**: [Open-Meteo](https://open-meteo.com/), CC BY 4.0.
- **District coordinates**: Open-Meteo geocoding (GeoNames, CC BY 4.0) and OpenStreetMap Nominatim (© OpenStreetMap contributors, ODbL). One district, Shahid Bhagat Singh Nagar, has no match and uses the average of Punjab's districts.
- **Soil N/P/K, pH, soil type and the market index** are state-level estimates, not measurements. The app says so, and points farmers to a Soil Health Card test.
