# Blogger home page build

Every daily run now also builds the home page with the gig rows and Event
structured data written into the HTML, then pushes it to Blogger.

    scraped_data/norwich_gigs.json
        -> blogger/build_blogger_page.py   (uses page_template.html + site_config.json)
        -> blogger/dist/page_body.html + page_meta.json
        -> blogger/publish_to_blogger.py   (Blogger API)

Run locally: `python3 blogger/build_blogger_page.py` (add `--today 2026-10-22` to test a date).

## What changed in the page
- Rows, month banners, counters and the `<h1>` are in the HTML. The page script no longer fetches GitHub; it only does search, Compact/Detailed view, calendar buttons, Spotify icons, hiding past gigs.
- Ticket links (the old `fixTicketUrls`) and `MusicEvent` JSON-LD are generated here, so the visible list and the markup can't disagree. JSON-LD covers the next 60 days (`JSONLD_DAYS`) and skips postponed/cancelled gigs.
- The `<!DOCTYPE>/<head>/<title>/<meta>/canonical/OG` block is gone from the body (Google ignores it there).

## Edit venue info
`blogger/site_config.json`: ticket pages, logos, full venue names, month banner images. Edit there, not in the template.

## One-off setup for auto-publishing
1. Google Cloud Console: create a project, enable **Blogger API v3**, create an OAuth client (type "Desktop app"), and add yourself as a test user on the consent screen. For a refresh token that doesn't expire after 7 days, set the consent screen to "In production".
2. Get a refresh token with scope `https://www.googleapis.com/auth/blogger` (e.g. via the OAuth Playground: tick "Use your own OAuth credentials", authorise, exchange the code, copy the refresh token).
3. Find your blog ID (in the Blogger dashboard URL: `blogger.com/blog/posts/<BLOG_ID>`) and the page ID (open the page in the editor: `.../edit/page/<PAGE_ID>`, or `/edit/post/<ID>` for a post).
4. GitHub repo -> Settings -> Secrets and variables -> Actions: add secrets `BLOGGER_CLIENT_ID`, `BLOGGER_CLIENT_SECRET`, `BLOGGER_REFRESH_TOKEN`, `BLOGGER_BLOG_ID`, `BLOGGER_PAGE_ID`. If it's a post, add a repository *variable* `BLOGGER_KIND` = `posts`.
5. Run the workflow manually once and check the "Publish the page to Blogger" step.

Until the secrets exist the publish step just skips. You can always paste `dist/page_body.html` into the Blogger HTML view by hand.

## Things the API can't do (set once in Blogger)
- **Meta description**: Page/Post settings -> Search description (for the homepage: Settings -> Search preferences -> Meta tags).
- **Canonical, OG tags, robots**: Blogger generates canonical itself; custom OG/Twitter tags need Theme -> Edit HTML.
- The page *title* is set by the publish script each run, with the current month.

## Check it worked
- `curl -s https://www.norwichgigs.com/ | grep -c 'data-iso-date'` should be in the hundreds.
- Search Console -> URL Inspection -> View crawled page; Rich Results Test for Event markup.

## Size
The body is ~600 KB (about 470 events). If Blogger ever rejects the update for size, lower `JSONLD_DAYS` or trim far-future events in `build_blogger_page.py`.
