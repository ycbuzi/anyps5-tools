# AnyPS5 screen: PS5 layout spec (11-15)

Measured at 1920x1080 from: Blu's Figma prototype (figma.com/community/file/1059509432545988663, CC BY 4.0) and PlayStation's
own screenshots in blog.playstation.com/2020/10/15/first-look-playstation-5s-next-generation-user-experience (home, control
center) plus playstation.com/en-us/support/games/customize-ps5-control-center (control order). Layout and behaviour follow the
PS5; logos, icons, sounds and fonts are our own (AnyPS5 mark, line icons, synthesized beeps, Segoe UI).
Page scale: 1rem = 16px at 1920 wide (html font-size 100vw/120).

## Boot
- Near-black background, soft light rays from the top left, warm bokeh band across the middle.
- Text "Press the PS button on your controller." centered at y 245, 30px, white 85%.
- Logo in a white circle (84px) at the center, y 560. Any button continues. Cross-dissolve ~1 s.

## User select
- Solid dark navy background. Clock top right (x right 70, y 60, 34px light).
- Title "Welcome back to AnyPS5" centered y 215, 64px light. Subtitle "Who's using this controller?" y 310, 30px.
- Controller glyph with player number "1" above the selected user, y 435.
- Row: "Add User" grey circle 190px with "+"; users 190px; selected user 260px with a 4px white ring. Labels under each
  (y 810, 26px); under the selected one an "Options" hint.

## Home (Games / Media)
- Top bar, centered on y 62: "Games" bold 34px at x 91, "Media" dim 34px at x 290. Right: search (x 1370), settings (1476),
  avatar 40px with green status dot (1579), clock 34px ending at x 1836. Plain white glyphs, no circles.
- Tile row: starts x 168, top y 125. Tiles 104px, radius 14px, gap 10px. Selected tile 175px (top-aligned, grows down),
  radius 22px, thin white border + glow; the tiles after it move right. The selected tile's name sits right of it,
  12px gap, centered on y 262 (just below the small tiles), 29px regular white. Last tile: Game Library.
- Background: the selected game's art full screen; light vignette left and bottom only; cross-fade ~0.6 s.
- Hero block (game): x 180; game title as big text where the PS5 shows the game logo (y ~560-680), meta line under it.
  "Play Game" pill ~290x70px, dark translucent, white text 32px; focused = white fill, dark text. "···" circle 70px beside it.
- Right card (Figma): x 1375-1745, y 360-730: captures image, under it progress-style stats (status / FPS / saves).
- Bottom row: label ("Must see" on the PS5) y 857, 26px; cards 235x132px, gap 31px, from x 173, y 900.
- Media tab: same top bar and row; selected tile has a soft lilac border; "Featured" row of captures at the bottom.

## Control center (PS button)
- The current screen stays visible; dark gradient rises from the bottom (from ~y 600).
- Cards row bottom-aligned at y 890, starting x 82: selected card 436x526px, others 360x400px, gap 14px, radius 19px,
  selected has a 2px white border. Card: small game icon top left, big value (e.g. "33%" 60px bold) with an outlined
  status badge, then label (dim 24px), title (bold 30px), subtitle (dim 24px).
- Under the selected card: a hint "(button) Resume" at y 926.
- Controls row: plain white glyphs (34px), 112px apart, centered on x 960, y 1008. PS5 order: Home, Switcher,
  Notifications, Game Base, Music, Sound, Mic, Accessories, Profile, Power. The Switcher shows the running game's tile.
  Ours (only what really works): Home, Switcher, Notifications, Machines (in Game Base's place), Screenshot, Pipeline,
  Sound, Accessories, Profile, Power. The focused control shows its name above it.

## Settings
- Full screen, dark blue gradient. Title "Settings" top left. Centered list of categories (icon + name), 70px rows;
  selected row lighter with a white glow. A category opens its own page: rows with the value or a toggle on the right.

## Settings (measured 11-15 from two PS5 console screenshots in bossrush.net's settings guide)
- Background #191b21 to #0f1117 with a warm spotlight top left, blue tint right, haze at the bottom.
- Title top left: x 96, y 103 center, 46px light.
- Category page: sections column at x 197, first at y 233, 104px apart, 30px; current section white, others 38% white.
- Options from x 672 to 1728, first row y 233, rows 98px with thin separator lines, labels 30px, values/toggles right-aligned 24px, small toggle.
- Top-level list (no screenshot found; built from memory of the PS5): centered column of categories with icons, same background.

## Game Library (measured from GameSpot's PS5 library screenshot)
- Header icon x 86, 'Game Library' x 173, y 86. Tabs at y 163 from x 173 (Your Collection, Installed, Plays), current white, others 40%.
- 'All: N' x 173, y 295; 'Sort by: ...' right-aligned at x 1747; round sort button x 86, y 372.
- Grid from x 173, top 336: 5 columns of 295px square tiles, 24px gaps, no text (the focused tile shows its name over its bottom edge).

## Game hub (measured from the PS5 hub screenshot in TweakTown's PS5 UI article, Destruction AllStars)
- Down from a game: the tile row and top bar slide up and away; a header band (dark gradient) shows the game's icon (79px, x 48) and name (36px, x 171, y 79).
- Hero: logo/title from y 369, tagline under it; Play pill 337x75 at y 603, '···' 75px beside it.
- Right: square box art 373px at x 1377-1750, y 186-560; stats under it (Progress / Earned on the PS5; ours Status / FPS / Saves).
- 'Activities' label at y 750; tall activity cards 365px wide, 32px gaps, from x 171.
- Ours: sections Activities (status, FPS, saves, last played), Captures, About. Down/up move between sections and the page scrolls; sections scroll under the header band (clipped there); title and Play fade once past the first section; button hints move into the band.

## Media tab (measured from the Figma prototype's Media frame at 1080p)
- Same top bar with 'Media' selected; same tile row; the selected media tile has a soft lilac border.
- Hero: title 55px light at x 187, y 540; description 31px, two lines, under it; the selected item's art fills the screen.
- 'Featured' label ~34px at x 175; cards 238x202px with a 62px white label strip, 31px gaps, from x 175.
- Ours: tiles Media Gallery (latest capture per game), Key Art, Box Art; Featured = that tile's pictures; a card opens a full-screen viewer (left/right to browse, Esc/B or a click to close). Row raised ~3rem above the Figma position so the button hints never overlap it.

## Control center, second pass (compared with three official shots: PlayStation blog Sackboy in-game, TweakTown home and in-game)
- Clock stays top right over a game. The game behind is barely dimmed (12%).
- Every card has its source's small icon top left (game icon, or the AnyPS5 mark for system cards); cards without a picture show a centered line graphic.
- Picking a control opens its control panel (titled with the control) above the control row, centered on the icon; the cards fade out while it is open, so the panel never covers cards or labels.

## Main Settings list and passcode page (measured from Android Central's PS5 screenshots)
- Settings list: centered, 1324px wide (82.75rem), rows 98px with thin separator lines, icon at x 323, text at x 394, first row centred at y 231. Selected row: thin white outline, faint fill (also on category pages).
- Passcode page: full page on the settings background; title top left (x 95, y 102); prompt centred at y 348; user picture 246px centred at x 662, y 631, name under it; four slots from x 951, y 492 (current = filled box with white outline, others = underlines); keypad panel 431x381 under them, 3 columns, digit + the controller button that types it (1 left, 2 up, 3 right, 4 down, 5 R1, 6 R2, 7 L1, 8 L2, 9 triangle, 0 square). On this page the controller buttons type digits; Circle/B goes back; a wrong code shakes the slots and says 'The passcode is incorrect.'
- Search, on-screen keyboard, notifications list, game info and the ··· menu were matched to web screenshots on 11-15 (next section). Still without a clean reference: the Information page rows below the art and the keyboard's symbol page.
- Keyboard (search and on-screen keyboard): Square deletes, Triangle adds a space, R2 is Done. These come from how the PS4/PS5 keyboard works, not from a screenshot. The legend shows them.

## Notifications, game options menu, Information, search (11-15, measured from web screenshots)
- Notifications (mekan0.com control-center shot): panel 653px wide, centred over the bell icon, top ~77px, bottom ~887px, dark grey, square corners. 'Notifications' header (dim), Do Not Disturb row with a toggle and 'stays on until you log out', then rows: 67px icon, source (small, dim), message (big), age at top right (Now/5m/2h/1d), line under the text. Newest first. Square deletes one, Options -> Delete All Notifications (Sony support page). Background dims.
- Game options menu (makeuseof.com home shot): opens beside the selected tile (left = tile right + 6px, top = tile top), 551px wide, rows 72px, 28px text, lines between groups, thin white outline on the selected row, background dims. Sub-choices open to the right (10scopes.com shot).
- Information (Capcom thumbnail + alvarotrigo.com composite): full page, 'Information' top left, 366px square art at 168,179, title right of it, then label/value rows 99px tall with lines and the outline selection.
- Search (howtogeek.com 4K shot): tabs at 172,62; field 172..1661 x 117..188, magnifier + 30px text; keyboard panel at 172,253, 871px wide, 11 columns x 4 rows (1-0 @ / q-p # / a-l ' / / z-m - _ ? !), 64px rows, 75px columns, light 26px glyphs, selected key = filled square with white outline; function cells below with button badges. Ours keeps the real keys only: Shift L2, Space triangle, Backspace square, Done R2 (no symbol page or cursor keys yet). Results sit right of the keyboard (below it in narrow windows).
- Keyboard pages and cursor (11-15): two pages, ABC and @#: (symbols ! " # $ % & ' ( ) * + / , - . / : ; < = > ? @ / [ \ ] ^ _ ` { | } ~ € / £ ¥ • … ° ± × ÷ ¡ ¿ §). Bottom rows: Shift L2, ABC, @#:, Space triangle, Backspace square / cursor left L1, cursor right R1, Done R2. Text is typed and deleted at the cursor, which blinks in the field.
- Accent page (11-15): third keyboard page 'àé' with 44 accented letters (à á â ä ã å æ ç è é ê / ë ì í î ï ñ ò ó ô ö õ / ø œ ù ú û ü ý ÿ ß ð þ / ā ē ī ō ū ł ś ź ż ğ ş); Shift makes them capitals. Bottom row now: Shift 2 cells, ABC, @#:, àé 1 each, Space 4, Backspace 2.
- Search tabs and history (11-15): tabs Games / Media (captures, key art, box art) / Players (profiles on this console); up from the keyboard's top row reaches the tabs, L1/R1 switch them from the results. With an empty field the results area shows 'Recent searches' (clock icon rows, newest first, max 10, per user) and 'Clear Search History'; Square deletes one. A search is saved on Done or when a result is opened. Search ignores accents and capitals.

## Trophies (11-15, Push Square overview + makeuseof game list, 4K)
- Overview: 'Trophies' top left; top right level badge + level, Total, platinum/gold/silver/bronze counts. Rows 142px from y 185 between x 170 and 1762: 100px icon, title + PS5/PS4 badge, platinum cup if earned, percentage with a bar under it, gold/silver/bronze earned. Sort (Options): Recently Earned / Progress / Alphabetical.
- Game list: icon + title top left; top right platinum cup, Progress % + bar, Earned x/y, gold/silver/bronze. 'All trophies: N' and 'Sort by: ...' above the list. Cards 173px tall, 22px apart: 123px trophy icon, name, grade line, earned or not; earned date right; divider; description. Locked trophies greyed; hidden unearned trophies show 'Hidden Trophy'. Sort: Game Default / Date Earned / Grade / Not Earned First. No rarity or compare (those need Sony's servers).
- Points 15/30/90/300 (bronze/silver/gold/platinum), progress % by points, PS5 level steps (60 a level to 100, then 90, 450, 900 ... 3600). Entry points: control center profile menu > Trophies, the game page's Trophies card. 'Trophy earned' pop-ups appear when AnyPS5 sees new entries in a game's _trophy file (data from trophies.py, every minute); not yet over a running game.

## Media Gallery (11-15, Sony's guide diagram + VG247 1459x811 + Trusted Reviews photo)
- 'Media Gallery' top left (x 90, y 95); tabs All / Favorites / Trophies / Albums under it (y 178, from x 163), the selected tab boxed; 'Sort by: Date' right (x 1753, y 290); round sort button left (57px at x 106, y 368). Grid from y 335 between x 163 and 1753: 3 columns of 16:9 tiles 509x286, gaps 28/33px; game name top left on each tile, trophy cup and favorite heart top right; selected tile outlined. Albums = one per game (+ Test Runs, the pipeline's latest shot per game). Options: Add to Favorites (per user) and Sort by Date (Newest/Oldest)/Game; the viewer's Options adds to Favorites. A trophy capture is saved automatically when a trophy is earned in a game. No video clips (AnyPS5 does not record video).

## Create menu (11-15, Eurogamer 1840x1070 shot)
- Bottom bar 270px tall across the screen, game visible above. Latest capture at the far left (245x114 at x 5); capture buttons centred around x 919; Capture Options at x 1764; the selected item's name above it; small menus open above their button. Ours: Recent Captures (opens the newest captures in the viewer), Take Screenshot, Capture Options (Screenshot File Type PNG/JPG, Save Trophy Screenshots, Show a Notification When Saved). No video buttons (no recording yet). Opened by the controller's Create button (DualSense/DS4, over USB or Bluetooth), Back tapped alone on an Xbox pad, or C on the keyboard; over a running game AnyPS5.exe opens it like the PS button opens the control center.

## Power menu and rest mode (11-15, TheGamer 1669x932 control center shot)
- Panel 657px wide at the right (4.8rem from the edge) above the power icon, 'Power' header, rows with icons: Enter Rest Mode (power symbol), Turn Off (circle), Restart (spinner); the selected row shows its description under the name. Ours: Enter Rest Mode pauses the running game (process suspended) and shows a black screen with a pulsing orange light; any button, key, click or the PS button wakes it and the game carries on. Turn Off closes the game and AnyPS5; Restart closes the game and starts AnyPS5 again. The pipeline is not touched. Settings > Power has the same three plus Switch User.

## Downloads in the control center (11-15)
- The PS5 control center's Downloads/Uploads: a control in the row (ours replaces the AnyPS5-only Machines button, so the row keeps 10 controls; 'Open the Machines Window' is the panel's last row) and a card for the leading item with its percentage. Panel like the notifications panel: header 'Downloads' (+ 'Pipeline paused'), rows with the game's icon, the kind (Copying from the PS5, Extracting, Building, Testing, Other machine) and since when, title, status, a progress bar (real percentage, or a moving bar when nothing gives one) and the percentage; as many rows as fit, then 'N more'. Data: downloads.py from the pipeline's own status files (console jobs in live\jobs with folder-size progress, game-watch.json, lanes\*.json, the build's [x/y] steps, machines.json).

## Settings depth (11-15)
- Main list in the PS5's order, only categories AnyPS5 can really do: Accessibility, Users and Accounts, System, Storage, Sound, Screen and Video, Accessories, Saved Data and Game Library, Captures and Broadcasts, Notifications, Test Pipeline. Left out on purpose: Network, Family and Parental Controls, Language (nothing real behind them yet). Every option does something: Accessibility (Reduce Motion, Bold Text, High Contrast), Users (Log In Automatically), System (24-Hour Clock, Enter Rest Mode After 20 min/1 h/3 h, Power), Storage (free space per drive from AnyPS5.exe), Sound (Trophy Sound), Captures (file type, trophy screenshots, notification, Create button Standard/Easy Screenshots), Notifications (pop-ups, display time, Do Not Disturb, trophy pop-ups over games, list). Settings switches were drawn as slivers (inline span); fixed, and the audit now flags a switch drawn too small.

## Accessibility (11-15, makeuseof 4K shot of PS5 Settings > Accessibility > Display)
- Sections Display / Screen Reader / Controllers. Display rows in the PS5's order: Zoom, Invert Colors, Color Correction (+ intensity), Text Size, Bold Text, High Contrast, Auto-Scroll Speed, Reduce Motion. Zoom: L3+R3 or Z magnifies 2x around the selection and follows it. Color Correction: daltonize matrices (Machado 2009 simulation, error moved to the other channels) for protanopia/deuteranopia/tritanopia, plus grayscale, as an SVG filter on the page. Text Size Large 1.15x / Larger 1.3x scales every body text rule under 2.6rem (not the home cards, the controls bar or the clock); the selftest audits Larger at 1280x720 and 734x1000. Auto-Scroll: a selected name that does not fit scrolls sideways (Slow/Normal/Fast). Screen Reader: speaks the selection with Windows voices (speechSynthesis), speed, volume, voice, read notifications. Controllers: Swap Cross and Circle (AnyPS5 screen only), Press-and-Hold Speed. Closed Captions and Chat Transcription wait for video and party chat.

## Motion (11-15)
- Already there: screen fade, selected tile grows, game row slides, name follows the tile, background crossfades, pop-ups slide in from the right. Added: game title and details fade up when the selected game changes (0.45 s), background art pushes in from 106% (1.6 s), control center cards and controls rise 3rem (0.32 s), menus and panels pop in (0.17 s), overlays fade in (0.2 s), screens zoom from 101.2% as they fade in. Lists that redraw on every press (settings, gallery, trophies, search) get no entrance animation (it would replay on each move). Reduce Motion turns all of it off. Still unmatched (no reference found 11-15): the game Information page layout beyond thumbnails, and the keyboard's symbol page set.
