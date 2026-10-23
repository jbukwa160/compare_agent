# Pre-order agent

Give it a supplier's price list. For every product it finds the prices in Bulgarian shops and in shops
worldwide, and how much people search for it. Then it scores the product 1-10 and gives a verdict:
**BUY**, **CONSIDER** or **SKIP**.

## First time only

1. Install Python 3.11 or newer.
2. Open a terminal in the `compare_agent` folder and run:
   ```bash
   pip install -r requirements.txt
   ```
3. Put the DataForSEO **API** login and password in `compare_agent/.env`. They are on
   https://app.dataforseo.com/api-access (the API password is not the website password). To check them:
   ```bash
   python -m compare_agent --check-keys
   ```
   It answers `OK` with the account balance, or says what is wrong.

## Run

1. Double-click **`run.bat`**, or drag a supplier file onto it.
2. Pick the supplier file. Any .xlsx, .xls or .csv works, and the columns can be in any order.
3. Choose **1** for Bulgaria only, or **2** for Bulgaria + worldwide.
4. Wait. Bulgaria only takes a few minutes; Bulgaria + worldwide takes about 10-15 minutes.
5. The report opens in Excel by itself.

Reports are saved in `compare_agent/output/` with the supplier file's name, the check and the time.
Old reports are never overwritten.

**Cost:** a worldwide run of about 35 products costs about $0.70 of DataForSEO credit; a Bulgaria-only
run costs a few cents. Running the same file again on the same day is almost free (results are kept for
12 hours).

## The report

The report has your file's columns first, then the agent's columns (blue headers). Row 2 explains each
column. Rows with a Dilon code (products we already sell) are tinted blue.

The **Shop prices** sheet lists every shop price that was used, with a link.

## Changing things

- **Websites:** `config/sites.yaml`. Set `enabled: false` to switch one off.
- **Sellers to ignore on Google Shopping** (second-hand shops): `exclude_sellers` in `config/sites.yaml`.
- **Score limits, VAT, exchange rates:** `config/settings.yaml`.

## If something goes wrong

- **A site blocked us:** the run still finishes and the other sites still count. The end of the run
  lists the sites that failed. Try again later.
- **"Wrong login" or "empty balance":** check `.env` with `python -m compare_agent --check-keys`.
- **The file isn't understood:** the message says which column name to add under `input.columns` in
  `config/settings.yaml`.

## Top products by traffic + EU vendors (separate tool)

Builds `output/Топ продукти по трафик*.xlsx`: the products with the most Google traffic at the leading Bulgarian
shops, and (sheet **EU доставчици**) which of them the EU vendors sell cheaper, with a 1-10 score.

```bash
python -m top_products                 # or double-click top_products.bat
```

- Put the **EU vendors** .zip from Google Drive in this folder, in `input/` or in Downloads (the newest one is
  used), or pass it: `python -m top_products --vendors "path/EU vendors.zip"`. This run is free.
- `python -m top_products --traffic` also fetches the traffic data again from DataForSEO. It costs about $6 and
  stops before passing `--budget` (default in `top_products/settings.yaml`). Within the same month it reuses the
  data it already paid for.
- Score rules, VAT and limits: `top_products/settings.yaml`. Shops: `top_products/shops.yaml`.
- Matches found only by name show as "по име – за проверка": check them, then list them in `top_products/review.yaml`.
