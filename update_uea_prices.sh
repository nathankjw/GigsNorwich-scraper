#!/bin/bash
set -e
cd ~/GigsNorwich-scraper
source ~/norwich-scraper/.venv-prices/bin/activate
git pull --rebase
python3 uea_price_cache.py
git add scraped_data/uea_prices.json
git commit -m "Update UEA price cache" || echo "No changes to commit"
git push
