# Nike

## SNKRS product feed script

Run the standalone Python script to fetch Nike SNKRS product threads and print
each product title, style-color code, and image URLs found in the thread nodes:

```bash
python3 nike_snkrs_feed.py
```

The script defaults to the US marketplace, English language, and the SNKRS app
consumer channel for `https://api.nike.com/product_feed/rollup_threads/v2`.
Use CLI flags to change the request:

```bash
python3 nike_snkrs_feed.py --marketplace GB --language en-GB --count 10
```
