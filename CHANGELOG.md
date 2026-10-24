# Changelog

## 2.5.0 (October 23, 2026)

A polish release: every page gone over on phone and desktop as if for a public launch, plus everything since 2.2.

Look and feel:

- One purple everywhere. The site's accent, buttons, pills and all five page headers now use the logo's exact purple, and the headers share one letter size.
- New header art for Showtimes, Festivals, Theatres and the dashboard.
- A proper footer: Browse, Theatres and cineclock columns, then the small print, on every page.
- Film pages use the full width on laptops: the film on the left, its showtimes beside it, staying in view as you scroll.
- The home page: Just added moves up, and new rows for the genre of the day (Spooky season all October), New releases and Classics on the big screen. The two link buttons at the bottom are gone; the menu covers them.
- A search or filter that matches nothing says so plainly, with one tap to clear.
- Showtimes lists every time for a film at once; the +N button that hid the third and later times is gone.
- Showings disappear the minute they start, on every page, without waiting for the next rebuild; finished days, empty theatres and old date chips go with them.
- Cloudflare's clock now starts the rebuilds every 4 hours and at 12:20 am, since GitHub's schedule often ran late or skipped.
- Theatre pages show every tag instead of cutting the row off; the home tiles' text lines up; festival dates read "Oct 23 to Nov 1 · 8 films".
- Tab titles end in "cineclock", and the Films tab lights up on film pages.

Accounts:

- Your account keeps your Letterboxd, IMDb profile, picture and rating choice, and brings them all back when you sign in on any device.
- Signing out clears your watchlists from that browser.
- Email settings say "Coming soon" until sending is switched on, instead of an internal note.

Data:

- The Park is read from its WordPress feed, like the Rio. Films are matched by the IMDb link on a theatre's page when there is one.
- Films TMDB's search can't find are looked up on Letterboxd, so titles like Begotten get posters and scores.
- Event screenings ("Food Stash Foundation...: Just Eat It") find the film inside the title and keep the theatre's own name and description.
- Nights that share a name at one theatre ("Shits n Giggles 2026: Opening Night", "...: Finals and Awards") get a festival page.
- A tag needs words in the listing behind it, so "35th anniversary" is never 35mm.
- AI tags run on Cloudflare Workers AI, re-read each listing weekly, and fall back to a second model if the first is down.

For search engines and phones:

- Film pages carry schema.org showtime data, so search results can show screenings.
- Skip to content link for keyboard users, and app shortcuts (Showtimes, Films, Festivals, dashboard) when cineclock is added to a home screen.

## 2.2.0 (October 20, 2026)

A hardening release from a full audit of the site, the relay and the pipeline.

- Festivals are in the main menu. On phones the menu moves to its own row under the wordmark so all four links fit, even on the smallest screens.
- Showtimes: the day chips stay on screen as you scroll and light up the day you're looking at; tapping one jumps there and keeps it lit. Bigger chips, visible keyboard focus, and no smooth scrolling for people who ask for less motion.
- Festival pages drop the repeated header art; the menu says where you are.
- Today, Tomorrow, Tonight and On soon use the build's fixed Vancouver offset, so phones with time zone data from before B.C. stayed on daylight time won't be an hour out after November 1.
- The spotlight's dots are bigger tap targets and are built safely from film titles.
- Theatre sites that are busy (429) or briefly down are asked again, twice, after a pause, before a theatre's update counts as failed.
- Every local link, script and image on every page is checked by a test, after one script was loading from the wrong folder.

Security and reliability:

- Unsubscribe links in emails now open a page with a button. Mail scanners follow links, and a link that unsubscribed straight away could turn people's emails off without them knowing. The mail app's one-click unsubscribe still works.
- The email job claims each person before sending, so two runs at once can't email anyone twice, and a failed send leaves them for the next run.
- Only an email address Google has verified is kept for an account.
- Connecting or syncing an IMDb watchlist needs a signed-in account, so nobody can fill the list of watchlists to read.
- Forcing a fresh Letterboxd read is limited to once every two minutes per profile.
- Error answers from the relay no longer include internal details, and oversized requests are turned away before they're read.
- CI and the API check run with read-only GitHub tokens; old http and github.io addresses are no longer trusted by the relay; an unused test dependency and some stray screenshots are gone.

Known limits: emails (alerts, digest, newsletter) are built and tested but don't send until a Resend key is added. The relay has no per-person rate limit beyond Cloudflare's own.

## 2.1.0 (October 20, 2026)

- Accounts: sign in with Google. Your settings, plans and watchlists follow you to any device, and Delete my account removes everything at once. A new privacy page says what's kept and where.
- Save marks the showings you're trying to make, with a purple ticket, kept apart from your Letterboxd and IMDb watchlists. Saving needs an account; tapping Save while signed out signs you in and brings you back with the film saved.
- Your cineclock is a dashboard: Letterboxd and IMDb watchlists (IMDb by profile link or file), a two-week calendar of everything you're after, rows for last chances, your plans and your watchlist, and a profile picture to pick from five movie-night textures.
- Email settings, one switch each: last chance, your films getting showtimes, a weekly digest and a monthly newsletter. Sending starts once email delivery is switched on.
- Home: a hero that shows one theatre and fits long titles, a hub of three tiles (what's on tonight, the festival of the moment, your next plan), and rows that show each film once. On soon drops films as their time passes.
- Festivals and series get their own pages, with the theatre's banner and blurb, and a page listing every one that's on.
- An AI read of each new listing catches tags the patterns miss (Shorts, live Music, festival names), and a Monday report flags listings worth checking.
- Showtimes: two times per film with a small +N for the rest, director and runtime under the title, and smaller posters on wide screens. Search, filters and sort share one line on desktop.
- Cards name their theatres in plain text with a coloured pin, use weekday names within the week, and stand-in posters are designed for films without one.
- Theatre pages: photos from Wikimedia Commons with credits, ticket prices, and a Showtimes row that jumps to the listings. VIFF times say when they're sold out.
- New header art on every main page.
- Under the hood: a check that warns when a theatre stops updating, renamed screenings keep their first-seen date, and GitHub Actions updated.

## 2.0.2 (October 19, 2026)

- Today, Tomorrow and Tonight are worked out in your browser from the real date, so a page built last night no longer calls today's shows "Tomorrow".

## 2.0.1 (October 18, 2026)

- Prices refresh themselves: once a month the update re-reads VIFF's, the Rio's and the Cinematheque's price pages and updates the numbers and the date checked, keeping the last good prices if a page changes. The Dunbar shows prices reported by local guides in 2024, labelled as such.
- Theatre pills: tags on screenings there are filled and open Films filtered to that theatre and tag; room features (7.1, Curved) are hollow outlines and come first, all on one scrolling row.
- Theatre pages have a Tickets section with prices and memberships, copied from each theatre's own site and dated, and the room specs move into a Tech specs section. Both stay folded until tapped.
- Theatre headings on Showtimes and film pages use each theatre's full logo.
- One-word tags: Music (was Live music), Atmos (was Dolby Atmos), and the theatre pills 7.1 and Curved.
- Your Letterboxd is simpler: enter your username and the page shows your watchlist films playing now and the films you've seen that are playing again, with a disconnect button. A username that isn't synced yet gets a link to ask for it. The export upload and the watchlist calendar feed are gone.
- A "Seen it" filter joins "On my watchlist".
- Every card always shows a score or Unrated, so no line is ever blank.
- Cards: anything special (35mm, Q&A, Festival) is a badge on the poster's corner, shown even when the film has a score, and the theatre logos move off the poster to their own line under the date.
- Film pages use the theatre's own description when TMDB has no synopsis, so one-off events like Vampyr × Applied Silence still say what they are.
- The README screenshots are taken from the live data by a Screenshots workflow in GitHub Actions.

## 2.0.0 (October 18, 2026)

A redesign around one idea: every film looks the same everywhere, so you always know where to look.

### Design

- One poster card across the whole site: home rows, theatre rows, the Films grid and the watchlist shelf. Every card has the same three lines: the title, the year with the score on the right, and the date. Theatre logos sit over the poster's corner.
- Cards show the Letterboxd score, or the next source that has one (IMDb, Rotten Tomatoes, Metacritic). With no score, a tag like Q&A or Festival takes its place, or a quiet Unrated pill.
- A film with only one screening gets a small purple star on its date and a highlighted booking button, instead of a separate badge.
- The Films page is a poster grid, three across on a phone.
- Tag pills share one look everywhere, in sentence case.

### Home

- A swipeable spotlight of three highlights: tonight's pick, then the standout screenings of the next two weeks. Every slide has full backdrop art, a sliver of the next one peeks in, and it wraps around. Arrow keys work too.
- A Letterboxd button beside the tagline, and three shortcuts: Showtimes, On film and Events.
- "Just added" ignores everything from a theatre's first scrape, so adding a theatre doesn't flood it.

### Theatres

- Theatre pages open with poster rows. "Now playing" always lists every film there, so even a two-film theatre gets a full row; Leaving soon, Events, On film and Just added appear only when they have enough to show.
- Theatres with more than one room swipe between rooms instead of using a dropdown.
- The Dunbar joins the Rio, the Park, the Cinematheque and VIFF Centre.

### Film pages

- Tap the poster to see it full size.
- Every score source still shows here, each linking out.

### Tags

- Shorter names: Q&A (filmmaker Q&As included), Intro, Music, Festival, Shorts, Event, Guests and Preview (advance, insider and sneak screenings).
- Festival programmes are tagged from the series the Cinematheque files them under, like "Small File Media Festival 2026".
- A listing with no year, no score and no tag is tagged Event.

### Letterboxd

- Connect a synced profile or upload your Letterboxd export (read in your browser, never uploaded) to see what's on your watchlist and what you've seen, with a "From your watchlist" row and a calendar feed of watchlist screenings.

### Sharing and finish

- Link previews: a shared page shows a proper card with a branded image, the film's backdrop or the theatre's photo.
- A 404 page that points you back to what's playing.
- A sitemap and canonical links for search engines.
- Date chips name the month once it changes ("Sun Nov 1").
- The version number in the footer.

## 1.0.0

The first version: scrapers for the Rio, the Park, the Cinematheque and VIFF Centre, tested against saved real responses; SQLite storage that updates instead of duplicating; film details from TMDB; scores from Letterboxd, IMDb, Rotten Tomatoes and Metacritic; home, Films, Showtimes, theatre and film pages; and a static site on GitHub Pages rebuilt every four hours.
