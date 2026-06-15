# Nike

## SNKRS product feed script

Install the Python dependencies:

```bash
python3 -m pip install -r requirements.txt
```

Run the standalone Python script to fetch Nike SNKRS product threads and save
new product assets locally:

```bash
python3 nike_snkrs_feed.py
```

The script defaults to the US marketplace, English language, and the SNKRS app
consumer channel for `https://api.nike.com/product_feed/rollup_threads/v2`.
Use CLI flags to change the request:

```bash
python3 nike_snkrs_feed.py --marketplace GB --language en-GB --count 10
```

On each run, the script creates a local SQLite database at
`nike_snkrs_assets.sqlite3` and an image folder at `static/images`. The
database table tracks:

- `style_code`
- `product_name`
- `image_path`

If a style code is not already in SQLite, the script downloads the first image
URL found in the product thread nodes, inserts the record, and prints:

```text
NEW ASSET SAVED: [Product Name]
```

Use `--database` or `--image-dir` to change where runtime assets are stored.

## FastAPI gallery

Start the web application with Uvicorn:

```bash
uvicorn nike_snkrs_feed:app --reload
```

Open `http://127.0.0.1:8000` to view a Jinja2-rendered gallery of downloaded
sneaker images. The homepage queries SQLite and displays all saved records in a
responsive CSS masonry grid, sorted by the most recently added assets first.
Images are served from `/static/images`.

By default, the app reads from `nike_snkrs_assets.sqlite3` and serves files from
`static/images`. Override these locations with environment variables:

```bash
SNKRS_DATABASE=/path/to/assets.sqlite3 \
SNKRS_IMAGE_DIR=/path/to/images \
uvicorn nike_snkrs_feed:app
```
