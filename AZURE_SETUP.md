# Setting up FarmSaarthi on Azure

This guide takes you from no account to a live app:

- photos in **Azure Blob Storage**, with records in **Azure Table Storage**
- advice translated by **Azure Translator**
- the app hosted on **Azure App Service**

Azure renames portal menus from time to time. If a label below doesn't match exactly, look for the nearest equivalent.

Use one **region** for everything. **Central India** is a good choice.

## 1. Create your Azure for Students account

1. Go to <https://azure.microsoft.com/free/students> and choose **Start free**.
2. Sign in with a Microsoft account, then verify with your **college email address**. No credit card is needed.
3. You get a credit allowance (US$100 at the time of writing) for 12 months. Check the current offer on the sign-up page.
4. In the portal (<https://portal.azure.com>), search **Cost Management**. Set a **budget alert** (for example US$10) so you get an email before the credit runs out.

## 2. Create a resource group

A resource group is a folder that holds everything FarmSaarthi uses.

1. Portal search: **Resource groups**, then **Create**.
2. Subscription: **Azure for Students**. Name: `farmsaarthi-rg`. Region: **Central India**.
3. Choose **Review + create**, then **Create**.

## 3. Create the storage account (photos and records)

1. Portal search: **Storage accounts**, then **Create**.
2. Fill in **Basics**:
   - Resource group: `farmsaarthi-rg`
   - Storage account name: lowercase letters and numbers, unique across Azure, for example `farmsaarthiphotos123`
   - Region: **Central India**
   - Primary service: **Azure Blob Storage or Azure Data Lake Storage Gen 2**
   - Performance: **Standard**
   - Redundancy: **Locally-redundant storage (LRS)**, the cheapest option
3. On **Advanced**, leave **Allow enabling anonymous access on individual containers** **off**. FarmSaarthi keeps photos private and shows them through expiring links.
4. Choose **Review + create**, then **Create**.
5. When it's ready, open the storage account and go to **Security + networking → Access keys**. Choose **Show** next to key1 and copy the **Connection string**. It starts with `DefaultEndpointsProtocol=https;AccountName=...`.

You don't need to create the container or the table yourself. On the first upload the app creates the private container `crop-photos` and the table `CropPhotos`.

To see uploads later, open the storage account and go to **Storage browser**:

- **Blob containers → crop-photos** holds the photo files.
- **Tables → CropPhotos** holds the upload records.

## 4. Create the Translator (for advice in every language)

1. Portal search: **Translators**, then **Create**.
2. Resource group: `farmsaarthi-rg`. Region: **Central India**. Name: for example `farmsaarthi-translator`. Pricing tier: **Free F0**, which allows 2 million characters a month.
3. Choose **Review + create**, then **Create**.
4. Open it and go to **Keys and Endpoint**. Copy **KEY 1** and the **Location/Region** (for example `centralindia`).

## 4b. Create Azure AI Speech (to read advice aloud)

1. Portal search: **Speech services**, then **Create**.
2. Resource group: `farmsaarthi-rg`. Region: **Central India**. Name: for example `farmsaarthi-speech`. Pricing tier: **Free F0**, which allows 0.5 million characters of neural voice a month.
3. Choose **Review + create**, then **Create**.
4. Open it and go to **Keys and Endpoint**. Copy **KEY 1** and the **Location/Region**.

FarmSaarthi saves every sentence it has already spoken in Blob Storage, so the same advice is never sent to Azure twice. If the free quota runs out, or the key is missing, the page uses the phone's own voice instead.

## 4c. Get a free data.gov.in API key (mandi prices and farmer questions)

This one isn't Azure. Mandi prices and Kisan Call Centre answers come from the Government of India's open data API.

1. Go to <https://data.gov.in> and choose **Sign Up**. It's free; you verify by email or mobile.
2. After logging in, open **My Account**. Your **API key** is shown there; copy it.
3. Use it as `DATA_GOV_IN_API_KEY` below.
4. Optional but recommended: run `python scripts/check_market_names.py` with the key set. It checks that each crop's name matches the government datasets. A count of 0 means a name needs fixing in `data/market_names.json`.

`.env.example` contains data.gov.in's public sample key so you can try things straight away. It returns at most 10 records per request and is shared by everyone, so it often hits its limit. Use your own key.

## 4d. Make the weather-alert keys

Browser notifications need a key pair. Make it once, on your own computer:

```bash
python scripts/make_vapid_keys.py
```

It prints `VAPID_PUBLIC_KEY=...` and `VAPID_PRIVATE_KEY=...`. Keep the private key secret. Also choose a `TASK_KEY`: any long random text, for example from `python -c "import secrets; print(secrets.token_hex(32))"`.

## 5. Try it locally with your real Azure resources (optional)

In your project's `.env` file (never commit it):

```
AZURE_STORAGE_CONNECTION_STRING=<connection string from step 3>
AZURE_TRANSLATOR_KEY=<KEY 1 from step 4>
AZURE_TRANSLATOR_REGION=centralindia
AZURE_SPEECH_KEY=<KEY 1 from step 4b>
AZURE_SPEECH_REGION=centralindia
SECRET_KEY=<any long random text>
DATA_GOV_IN_API_KEY=<key from step 4c>
VAPID_PUBLIC_KEY=<from step 4d>
VAPID_PRIVATE_KEY=<from step 4d>
VAPID_SUBJECT=http://localhost:8000
TASK_KEY=<any long random text>
```

To make a random `SECRET_KEY`, run `python -c "import secrets; print(secrets.token_hex(32))"`.

Run `python app.py`, upload a photo, and check it appears in **Storage browser**.

## 6. Host the app on Azure App Service

1. Portal search: **App Services**, then **Create → Web App**.
2. Fill in **Basics**:
   - Resource group: `farmsaarthi-rg`
   - Name: for example `farmsaarthi-<yourname>`. This becomes `https://farmsaarthi-<yourname>.azurewebsites.net`.
   - Publish: **Code**
   - Runtime stack: **Python 3.12**
   - Operating system: **Linux**
   - Region: **Central India**
   - Pricing plan: create a new plan and pick **Free F1**
3. Choose **Review + create**, then **Create**.
4. Open the web app and go to **Settings → Environment variables → App settings**. Add each of these with **+ Add**, then **Apply**:

   | Name | Value |
   | --- | --- |
   | `AZURE_STORAGE_CONNECTION_STRING` | connection string from step 3 |
   | `AZURE_TRANSLATOR_KEY` | KEY 1 from step 4 |
   | `AZURE_TRANSLATOR_REGION` | `centralindia` |
   | `AZURE_SPEECH_KEY` | KEY 1 from step 4b |
   | `AZURE_SPEECH_REGION` | `centralindia` |
   | `SECRET_KEY` | a long random text (see step 5) |
   | `DATA_GOV_IN_API_KEY` | key from step 4c |
   | `VAPID_PUBLIC_KEY` | from step 4d |
   | `VAPID_PRIVATE_KEY` | from step 4d |
   | `VAPID_SUBJECT` | `https://<your-app-name>.azurewebsites.net` |
   | `TASK_KEY` | the task key from step 4d |
   | `SCM_DO_BUILD_DURING_DEPLOYMENT` | `true` |

5. Go to **Settings → Configuration → General settings**:
   - **Startup Command**: `gunicorn --bind=0.0.0.0 --timeout 600 --workers 1 --threads 4 app:app`
   - **SCM Basic Auth Publishing Credentials**: **On**. The GitHub deploy below needs it.
   - Choose **Save**.
6. Optional: under **Monitoring → Health check**, set the path to `/healthz`.

## 7. Connect GitHub so every push to `main` deploys

1. In the web app's **Overview**, choose **Download publish profile**. You get a `.PublishSettings` file. Treat it like a password.
2. On GitHub, open the FarmSaarthi repository and go to **Settings → Secrets and variables → Actions**.
   - **Secrets** tab → **New repository secret**. Name: `AZURE_WEBAPP_PUBLISH_PROFILE`. Value: the whole contents of the downloaded file.
   - **Variables** tab → **New repository variable**. Name: `AZURE_WEBAPP_NAME`. Value: your web app name, for example `farmsaarthi-<yourname>`.
3. Push to `main`, or open **Actions → Deploy to Azure App Service → Run workflow**. The workflow runs the tests first and only deploys if they pass.
4. Open `https://<your-app-name>.azurewebsites.net/healthz`. Every service should say `true`: `photo_storage`, `translator`, `speech`, `feedback`, `prices`, `ask` and `alerts`.
5. Usage and feedback totals for your report are at `https://<your-app-name>.azurewebsites.net/stats`. Written comments are in the storage account under **Storage browser → Tables → AppEvents**.

The first start after a deploy can take a minute or two while Azure installs the packages.

## 8. Turn on the daily weather alerts

1. On GitHub, go to **Settings → Secrets and variables → Actions**.
   - **Variables** tab: add `APP_URL` = `https://<your-app-name>.azurewebsites.net`.
   - **Secrets** tab: add `TASK_KEY` with the same value as in App Service.
2. Open **Actions → Daily weather alerts → Run workflow** once to test it. The log shows how many districts were checked and how many alerts were sent.

From then on it runs every morning at 06:00 India time, for free. GitHub pauses scheduled workflows in repositories with no activity for 60 days, so push something or run it by hand now and then.

Notifications work in Chrome on Android, and on iPhone only after the app has been added to the home screen.

## Keeping keys safe

- Keys belong in App Service **App settings** and your local `.env`, never in code or Git.
- If a key leaks, go to the resource's **Access keys** or **Keys and Endpoint** page and choose **Rotate** or **Regenerate**. Then update the App setting.
- The Free F1 plan sleeps after inactivity, so the first visit after a break is slow. That's normal.

## Moving off Render

Once the Azure site works, you can delete the old Render service. The app still runs on Render if you want to keep both. Render needs the same environment variables and the start command `gunicorn app:app`.
