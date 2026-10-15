# Marstoy → LEGO set lookup

Builds one offline HTML file (plus an optional PDF) that lists every item in the
Marstoy `brick-kits` collection next to its official LEGO set. It works out the
set number by dropping the `M` and reversing the digits (M06457 → 75460), then
checks that number on Rebrickable.

Run it on your own computer (it needs internet access):

```bash
pip install requests beautifulsoup4
pip install playwright && playwright install chromium   # only needed for --pdf
export REBRICKABLE_KEY=your_key   # free: Rebrickable → Settings → API
python lego_lookup/build_catalog.py --pdf
```

Output goes to `output/`:
- `marstoy_lego_catalog.html` is self-contained, with the images embedded. AirDrop
  it or save it to iCloud, then open it from the Files app on your iPhone.
- `marstoy_lego_catalog.pdf` is the PDF copy.
- `items.json` holds the raw data. Codes that don't map to a real set are flagged
  and listed at the end of the run.

Without a `REBRICKABLE_KEY`, the script still matches images through the public
CDN, but it shows Marstoy's titles instead of the official LEGO names.

If the crawl finds 0 items, try `--html-only`. Marstoy's page markup may have
changed, and the HTML selectors in `crawl_html` may need adjusting.
