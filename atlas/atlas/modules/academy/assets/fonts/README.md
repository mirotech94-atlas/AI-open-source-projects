# Fonts

Cabinet Grotesk and Atkinson Hyperlegible Next (`cabinet-*.woff2`,
`atkinson-next-var.woff`, licence `OFL.txt`) are the store's faces.

IBM Plex is the video system's face (Miro, 2026-10-17: tutorial videos,
thumbnails and teaching Shorts; see `docs/academy-video-system/`). Licence:
SIL Open Font License 1.1, `OFL-plex.txt` (the packages' own LICENSE.txt).
Files are copied unchanged and renamed by weight.

| File | Source | Version | sha256 |
|---|---|---|---|
| plex-sans-400.woff2 | npm `@ibm/plex-sans`, fonts/complete/woff2/IBMPlexSans-Regular.woff2 | 1.1.0 | ba711a3085ff9f27440b6b9c4550cfc47c97bf36591d5da958b975bb3add8c1a |
| plex-sans-500.woff2 | npm `@ibm/plex-sans`, fonts/complete/woff2/IBMPlexSans-Medium.woff2 | 1.1.0 | 5660f8a658f8bb50dbc005232f885eadffd2bc1c235c4f6fbb63469d1f9cde6d |
| plex-sans-600.woff2 | npm `@ibm/plex-sans`, fonts/complete/woff2/IBMPlexSans-SemiBold.woff2 | 1.1.0 | f78048030eab62e860efa39a0df79e2e5581bf122eb95b9bc42c0b8a4988d205 |
| plex-mono-400.woff2 | npm `@ibm/plex-mono`, fonts/complete/woff2/IBMPlexMono-Regular.woff2 | 2.5.0 | ba204497f16b6d334cee9d1e963a831b73e3a56e1d6300a8489d18df7214b350 |
| plex-mono-500.woff2 | npm `@ibm/plex-mono`, fonts/complete/woff2/IBMPlexMono-Medium.woff2 | 2.5.0 | 33faf307fa6031fb4062276d7320a6d632de890cbb347576fd80cfa01077bc25 |
| libass/plex-sans-500.ttf | GitHub release `@ibm/plex-sans@1.1.0`, ibm-plex-sans.zip, fonts/complete/ttf/IBMPlexSans-Medium.ttf | 1.1.0 | 331c8639d7598b2cde62a911a71db195e30cb655cd6bdf2e324a7e984955f907 |

Tarballs:
- https://registry.npmjs.org/@ibm/plex-sans/-/plex-sans-1.1.0.tgz (published 2024-11-11)
- https://registry.npmjs.org/@ibm/plex-mono/-/plex-mono-2.5.0.tgz (published 2026-06-11)
- https://github.com/IBM/plex/releases/download/%40ibm/plex-sans%401.1.0/ibm-plex-sans.zip

Both npm packages point at https://github.com/ibm/plex.

Why a TTF: the Short captions are burned by libass (ffmpeg's `ass` filter,
`fontsdir=` this `libass/` folder), and FreeType here cannot open woff2
("Error opening memory font"). libass matches the TTF by its legacy family
name, `IBM Plex Sans Medm`, and sizes by ascender + descender (1.3 em), so
the caption's ASS Fontsize is 1.3 x its px size.
