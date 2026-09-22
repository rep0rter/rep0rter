# rep0rter — three-minute introduction

Open `/ppt`. Use **← / →** to move, **Home / End** to jump, or the numbered buttons. Each slide has a direct link (`/ppt#slide-3`). The four embedded demo views on slide 3 advance with the same left/right keys.

## 1. Community updates, connected — 0:00–0:20

“This is rep0rter, with a zero. Civic tech communities share ideas, events, and projects every day. But following them can mean moving between platforms and languages. rep0rter brings those updates into one readable place, so catching up can become the first step toward joining in.”

## 2. The communities — 0:20–0:45

“Think about communities such as g0v in Taiwan, Code for Japan, and Code for Korea. People work in different local contexts, but many of their questions are shared. How can technology help people participate? How can knowledge travel? The database already includes updates from all three, together in one timeline. Four reading languages make it easier to learn what another community is doing.”

## 3. The experience — 0:45–1:30

Stay on slide 3. Each press of **→** advances the embedded demo; **←** reverses it. No separate page is needed.

- **0:45–0:56, Latest updates:** “Here is our actual reader. We can see community updates, dates, and links back to the original sources.”
- **0:56–1:07, Search and filters:** Press →. “This is the real filter panel. Here we narrow the timeline to a source already in our data. Readers can also search by topic, date, or author.”
- **1:07–1:18, Japanese:** Press →. “The same reader is available in Japanese. We also support English, Traditional Chinese, and Korean.”
- **1:18–1:30, Dark appearance:** Press →. “The interface adapts to light or dark reading. The goal is simple: make community activity easier to discover and easier to follow.”

Press → once more to continue to slide 4. The embedded view uses actual published pages and data; the demonstration does not change the viewer’s saved theme preference.

## 4. Built for readers — 1:30–2:05

“Readers can use English, Traditional Chinese, Japanese, or Korean. Search and filters help narrow the timeline by date, topic, author, and source. Original links keep the context available, and RSS makes it possible to follow updates in your own reader. There is also a Google sign-in and contribution flow, so the project can support people sharing updates as well as reading them. Machine translation is useful for discovery; the original is still the reference for precise details.”

## 5. Under the hood — 2:05–2:35

“The pipeline starts with supported public-source integrations, including Slack, GitHub, Mastodon, RSS, and public Notion pages. Python and SQLite organize the collected information, and a language model helps summarize and translate it. The project supports publishing through the website, RSS, and Telegram. Cloudflare Workers serves the website, while GitHub Actions runs the hourly reporting job. The reader uses Jinja templates, TypeScript, and CSS.”

## 6. The people — 2:35–3:00

“Behind this project are the people shown here, and the communities whose work makes it useful. rep0rter is an invitation to stay connected: bring an update, discover a project, or start a conversation. Open the site, find something that interests you, and follow it back to the community. Thank you.”

Name the credited people as shown on the slide. Do not add unverified affiliations or roles. The six timings total **180 seconds**; the script leaves space to gesture toward the interface and the team.

## Credits and implementation reference

Team roles are transcribed from the project HackMD notes dated **2026-09-19**: Sky Hong — CI/Infra; PGpenguin72 — UI Design; Natsumi — Tracking Code for Japan Slack integration; Erika — Improved translation; aoi — Added Code for Japan Notion content; boyce — Connecting posts to Threads; Han — Bug fixes and Korean translation; Seo hyun — Advising. The on-screen Project notes link points to the supplied source. Tracking an integration or connecting a service describes ongoing work, not a shipped capability.

The project uses Python, Jinja templates, vanilla TypeScript and CSS, and SQLite. The deployment uses native Cloudflare Workers with an hourly GitHub Actions reporting job. Public community sources include Code for Japan Notion and Code for Korea RSS/Medium per the September 19 project notes. Code for Japan Slack and the Threads connection were described as ongoing work.
