# The Norwich Gigs website (GitHub Pages)

The whole site is built every day by the workflow and published on GitHub
Pages. (The folder is still called `gig_pages` because that's where it
started.)

    scraped_data/norwich_gigs.json  (+ details from event_details.py)
        -> gig_pages/build_gig_pages.py
             uses blogger/page_template.html   the home page (search, calendar, gig cards)
                  gig_pages/style.css          the look (matches the old Blogger theme)
                  site_pages/                  About Us, Venues, Advertise... (edit these)
                  blogger/site_config.json     venue names / logos / links, GIG_PAGES settings
        -> gig_pages/dist/                     (not committed; uploaded by the workflow)
        -> deploy-gig-pages job                (GitHub Pages)

What's in the site:

| Address | What it is |
|---|---|
| `/` | Home page: search, Tonight / This weekend / calendar, gig cards |
| `/gigs/` | Every upcoming gig, by month |
| `/gigs/2026-11-26-parker-barrow-waterfront/` | One page per gig, plus `gig.ics` for calendars |
| `/p/aboutus.html` etc. | The pages from `site_pages/p/`, at the same addresses as on Blogger |
| old post addresses | Forward to the home page (listed in `site_pages/redirects.json`) |
| `/sitemap.xml`, `/robots.txt`, `/404.html` | For search engines / missing pages |

Build locally: `python3 gig_pages/build_gig_pages.py` (add `--today 2026-11-25`
to test a date), then serve `gig_pages/dist` (e.g. `python3 -m http.server -d gig_pages/dist`).

## Editing pages

- **About Us, Venues, Advertise, List Your Gig, Privacy policy**: edit the files
  in `site_pages/p/` on GitHub (pencil icon). The first line,
  `<!-- title: About Us -->`, is the page title. To add a page, add a file the
  same way; it appears at `/p/<file name>`.
- **Home page layout / search / calendar**: `blogger/page_template.html`.
- **Gig page layout, header, menu, sidebar, footer**: `gig_pages/build_gig_pages.py`
  (the menu links, logo and sidebar text are near the top) and `gig_pages/style.css`.

## Switching www.norwichgigs.com from Blogger to GitHub

Do this once the site looks right at https://gigs.norwichgigs.com.

1. **Settings in the repo.** In `blogger/site_config.json` under `GIG_PAGES`, set
   `"site_url": "https://www.norwichgigs.com"`. Commit.
2. **DNS** (where the domain is registered):
   - Change the `www` CNAME record from `ghs.google.com` to `<username>.github.io`.
   - Add four `A` records for the bare domain (`@`, i.e. norwichgigs.com):
     `185.199.108.153`, `185.199.109.153`, `185.199.110.153`, `185.199.111.153`.
     Remove any other `A` records for `@` (e.g. Blogger's or the registrar's
     forwarding). GitHub then sends norwichgigs.com on to www.
3. **GitHub**: Settings -> Pages -> Custom domain: change it to
   `www.norwichgigs.com` -> Save. Tick **Enforce HTTPS** once it's offered
   (can take up to an hour).
4. **Blogger**: Settings -> Publishing -> Custom domain: remove
   www.norwichgigs.com, so Blogger stops claiming it. Don't delete the blog.
5. **Run the workflow** (Actions -> Run workflow) and check the site.
6. **Remove the Blogger steps** from `.github/workflows/scrape.yml`: delete the
   two steps "Build the home page HTML (events included)" and "Publish the
   page to Blogger". (Optional, but otherwise they keep updating the old blog
   and fail once its login expires.)
7. **Google Search Console**: add/verify `https://www.norwichgigs.com` if it
   isn't already, and submit `https://www.norwichgigs.com/sitemap.xml`.

**To go back to Blogger** at any point: point the `www` CNAME back to
`ghs.google.com`, and re-add the custom domain in Blogger's settings. The blog
is untouched.

Note: after the switch, `gigs.norwichgigs.com` stops working (a GitHub site
has one address). You can delete its DNS record.
