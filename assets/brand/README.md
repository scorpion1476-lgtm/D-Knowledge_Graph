# D-Knowledge Graph brand assets

Short guide. One canonical file, everything else derived from it.

## The canonical file

`logo.png` is the identity: a grayscale "D-Knowledge Graph" wordmark with a
circuit-node speech-bubble motif at its right end, on a transparent background.
It is the only hand-authored asset in this folder.

Every other image here is generated from it by
`scripts/build_brand_assets.py`. Do not hand-edit them; change `logo.png` and
re-run the script.

## Which asset goes where

- **Wordmark** (wide). README masthead, docs banners, slides, any wide
  context. Files:
  - `logo.png` for raster use, and the file the README renders
  - `logo.svg` for scalable use (the raster is embedded as a `data:` URI,
    so it fetches nothing)

- **Emblem** (square). The speech-bubble motif cropped square out of the
  wordmark. Use for favicons, docs sidebar logos, app icons, package
  thumbnails, any small or square context. Files:
  - `emblem.svg` for scalable use
  - `emblem_512.png` for high-DPI raster
  - `emblem_256.png`, `emblem_128.png`, `emblem_64.png`,
    `emblem_48.png`, `emblem_32.png`, `emblem_16.png` for fixed sizes
  - `favicon.ico` for browser tabs (embeds 16, 32, 48)

- **Social preview**. GitHub repo social preview only. File:
  - `github_social_preview.png` (1280 x 640)
  - Upload via Settings, General, Social preview. See
    `SET_SOCIAL_PREVIEW.txt` for the exact one step web procedure.

## Rules

- Emblem for square and small contexts. Wordmark for wide headers.
- Do not stretch either asset. Scale proportionally only.
- Do not recolour. The identity is grayscale on a transparent background
  and there is no coloured variant. An earlier build shipped a blue
  circular emblem next to this wordmark; the two were never one identity,
  the README masthead ended up rendering the wrong one, and that mark has
  been removed.
- Do not add drop shadows or effects. The mark already carries the
  intended visual weight.
