# Gig pages (gigs.norwichgigs.com)

One page per gig, built every day by the workflow and published on GitHub
Pages. Blogger keeps serving the home page at www.norwichgigs.com.

    scraped_data/norwich_gigs.json  (+ details from event_details.py)
        -> gig_pages/build_gig_pages.py   (uses style.css + blogger/site_config.json)
        -> gig_pages/dist/                (not committed; uploaded by the workflow)
        -> deploy-gig-pages job           (GitHub Pages)

Each gig page has the poster, date, venue, door / start / end times, age,
price, support acts, a short excerpt from the venue's write-up (with a link
back), Get tickets, Add to calendar (.ics), Spotify, more gigs at the same
venue and on the same night, and MusicEvent structured data for Google.
Gigs from the last 30 days keep a page marked "already happened" (hidden
from search engines) so links people shared still work.

Addresses look like `/gigs/2026-11-26-parker-barrow-waterfront/` (date, title,
venue). If a venue renames a gig, its address changes and the old one shows
the "gig not found" page.

Build locally: `python3 gig_pages/build_gig_pages.py` (add `--today 2026-11-25`
to test a date), then open `gig_pages/dist/index.html`.

## One-off setup

1. **Turn on Pages.** Repo -> Settings -> Pages -> Build and deployment ->
   Source: **GitHub Actions**.
2. **Run the workflow** (Actions -> Daily Norwich Gigs Scrape -> Run workflow).
   When it finishes, the `deploy-gig-pages` job shows the address, something
   like `https://<username>.github.io/<repo>/`. Check a few pages there.
3. **Point the subdomain at GitHub.** Where norwichgigs.com is registered, add a
   DNS record: type `CNAME`, name `gigs`, value `<username>.github.io`.
   This doesn't touch the `www` record, so Blogger carries on as before.
4. **Add the custom domain.** Settings -> Pages -> Custom domain:
   `gigs.norwichgigs.com` -> Save. When the DNS check passes, tick
   **Enforce HTTPS** (can take up to an hour to become available).
5. **Link the home page to the gig pages.** In `blogger/site_config.json`, set
   `"link_from_home": true` under `GIG_PAGES`. From the next run, clicking a
   card on the home page opens its gig page; the ticket button on each card
   still goes straight to the ticket site.
6. **Tell Google.** In Search Console, add `https://gigs.norwichgigs.com` as a
   property and submit `https://gigs.norwichgigs.com/sitemap.xml`.

Settings in `blogger/site_config.json` -> `GIG_PAGES`:
- `site_url` - the gig pages' address, used for canonical links, the sitemap
  and the home page links.
- `home_url` - where "Norwich Gigs" in the top bar goes.
- `link_from_home` - see step 5.
- `past_days` - how long a gig's page stays up after the gig.
