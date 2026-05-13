# Frontend spec — Landing page

## Goal

One-screen marketing page for the Telegram bot. Single purpose: get the visitor into the bot.

## Page structure (single fold, no scroll required)

1. **Logo / wordmark** — top-left, small. Text: `CaffeBot` (or final name).
2. **Hero headline** — single line, large. Ukrainian.
   - "Твоя кавомашина зламалась? Запитай бота."
3. **Sub-headline** — one supporting line, regular weight.
   - "Допомагаю з 21 брендом кавомашин. Працює в Telegram. Безкоштовно."
4. **Primary CTA** — single big button, centred under sub-headline.
   - Label: `Відкрити в Telegram`
   - Link: `https://t.me/{{BOT_USERNAME}}` (placeholder — fill before deploy)
   - Optional Telegram icon inline left of label.
5. **Footer** — single small line, bottom-centre.
   - "Дипломна робота · {{YEAR}} · {{AUTHOR}}"

Nothing else. No nav, no carousel, no feature grid, no testimonials, no screenshots.

## Visual style

- **Vibe:** minimalist, generous whitespace, calm. Premium-coffee feel, not tech-startup gradients.
- **Palette:** off-white background (`#F7F4EF` or `#FAFAF7`), espresso-brown accents (`#3B2A20`), single warm CTA colour (`#C97B4A` or similar terracotta). One accent only.
- **Typography:** one serif for headline (e.g. *Fraunces*, *Source Serif*), one sans for body/CTA (e.g. *Inter*, *Geist*). Two fonts max.
- **Headline size:** 56-72 px desktop, 36-44 px mobile.
- **Button:** solid fill, rounded `~12 px`, generous padding (~18 px vertical / 32 px horizontal), no shadow or very subtle. Hover = darken 6-8%.
- **No imagery** in v1. Optional later: small espresso-cup line illustration top-right, mono-stroke.

## Responsive

- Desktop ≥1024 px: centred column, max-width ~720 px.
- Mobile <768 px: same vertical layout, headline scales down, CTA button full-width minus 32 px gutters.

## Tech notes

- Single static HTML page or Next.js app-router landing route (`/`).
- No client JS required for v1. Plain `<a href="https://t.me/...">` button.
- Lighthouse target: Performance ≥95, Accessibility ≥95.
- Meta tags: Open Graph image (1200×630) for shareable link previews. Skip if out of scope.

## Acceptance checklist

- [ ] One screen, no scroll on a 1366×768 laptop.
- [ ] CTA button is the visual centre of gravity.
- [ ] Tab order: logo → headline (h1) → CTA → footer.
- [ ] CTA is a real `<a>` (not `<button>`), opens `t.me` link in same tab on mobile, new tab on desktop.
- [ ] Page weighs <50 KB total (no fonts beyond Google Fonts woff2 subset).

---

## Mockup prompt for Claude design

Paste this into the design model that generates mockups (Claude design / image-gen).

> Design a single-screen landing page mockup for a Ukrainian Telegram chatbot called **CaffeBot**. The bot diagnoses coffee-machine problems for 21 brands. The page has one purpose: get the visitor to open the Telegram bot.
>
> **Layout** — vertical, centred, single fold (no scroll). Generous whitespace.
>
> **Content, top to bottom:**
> 1. Small wordmark "CaffeBot" in the top-left corner, espresso-brown.
> 2. Big serif headline, centred: **"Твоя кавомашина зламалась? Запитай бота."**
> 3. One quiet sub-line below, sans-serif: "Допомагаю з 21 брендом кавомашин. Працює в Telegram. Безкоштовно."
> 4. One large solid CTA button below, terracotta orange, white label "Відкрити в Telegram" with a Telegram paper-plane icon on the left.
> 5. Tiny footer line bottom-centre: "Дипломна робота · 2026".
>
> **Style** — minimalist, premium-coffee aesthetic. Off-white background (`#F7F4EF`). Espresso-brown text (`#3B2A20`). One accent colour for the CTA only (`#C97B4A`). Two fonts max: a warm serif for the headline (Fraunces or similar), Inter for everything else. No drop shadows, no gradients, no illustrations, no photos.
>
> **Constraints** — desktop viewport 1440×900. No navbar. No feature grid. No carousel. No screenshots of the bot. The CTA button is the visual centre of gravity — give it room to breathe.
>
> **Output** — clean PNG mockup, pixel-aligned, ready to hand to a frontend engineer.
