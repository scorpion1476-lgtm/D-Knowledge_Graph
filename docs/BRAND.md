# D-Knowledge Graph brand

The project uses a small identity created for this project alone. No
asset here is copied from any other product.

## Primary assets

The identity is one file. `assets/brand/logo.png` is the canonical grayscale
"D-Knowledge Graph" wordmark, with a circuit-node speech-bubble motif at its
right end, on a transparent background. Every other asset below is **derived
from it** by `scripts/build_brand_assets.py`, so the set cannot drift apart.
Rebuild them with:

```bash
python scripts/build_brand_assets.py
```

| Asset | File | Use |
|-------|------|-----|
| Wordmark, PNG (canonical) | `assets/brand/logo.png` | README masthead, docs header, any wide context |
| Wordmark, SVG | `assets/brand/logo.svg` | Wide headers when SVG is supported |
| Emblem, SVG | `assets/brand/emblem.svg` | Square contexts, favicons, docs sidebar |
| Emblem, 512 PNG | `assets/brand/emblem_512.png` | High-DPI app icon, CLI docs image |
| Favicon | `assets/brand/favicon.ico` | Browser tabs, 16/32/48 embedded |
| Social preview | `assets/brand/github_social_preview.png` | GitHub repo Social preview upload |

The emblem is the speech-bubble motif cropped square out of the wordmark. It is
not a separate mark and there is no second, coloured emblem: an earlier build
carried a blue circular emblem alongside the wordmark, the two were never the
same identity, and the README masthead ended up rendering the wrong one. That
mark is gone from the tree and the derivation script is what keeps it gone.

See `assets/brand/README.md` for the short usage guide.

## Wordmark (ASCII)

```
 _____     _  __                        _          _              ____                 _
|  __ \   | |/ /                       | |        | |            / ___|  _ __ __ _  _ __ | |__
| |  | |  | ' /  _ __   _____      __ _| | ___  __| | __ _  ___ | |  _  | '__/ _` || '_ \| '_ \
| |  | |  | . \ | '_ \ / _ \ \ /\ / // _` |/ _ \/ _` |/ _` |/ _ \| |_| | | | | (_| || |_) | | | |
| |__| |  | |\ \| | | | (_) \ V  V /| (_| |  __/ (_| | (_| |  __/ \____| |_|  \__,_|| .__/|_| |_|
|_____/   |_| \_\_| |_|\___/ \_/\_/  \__,_|\___|\__,_|\__, |\___|                    |_|
                                                       __/ |
                                                      |___/
```

## Wordmark (SVG)

`docs/brand/dkg-wordmark.svg` renders a plain SVG version of the name in
a monospace stack. It is 480x64 pixels, uses only the presentation
foreground colour, and has no linked fonts.

## Colour tokens

The identity itself is monochrome: `assets/brand/logo.png` and everything
derived from it use greys and nothing else, and the build script refuses to run
if the source file has a single opaque pixel whose channels are unequal. The
diagrams in the README follow the same rule, every shape a shade of grey.

The tokens below exist for *interface* states in any future themed output, not
for the mark. Documentation and generated reports stay colourless.

| Token | Value | Use |
|-------|-------|-----|
| `dkg-ink` | `#0f172a` | Foreground text |
| `dkg-paper` | `#f8fafc` | Background |
| `dkg-accent` | `#0ea5e9` | Selected state, links |
| `dkg-warn` | `#eab308` | Warnings only |
| `dkg-error` | `#dc2626` | Errors only |

Diagram greys, used by every Mermaid figure in the README. Every one has equal
red, green and blue channels, so "grey" is a literal description rather than an
approximate one. An earlier set used the near-neutral zinc ramp, whose channels
differ by up to 9; a review pointed out that calling that grayscale was the
wrong word, and it was easier to make the claim true than to qualify it.

| Role | Value |
|-------|-------|
| Grouping or outer shape fill | `#f4f4f4` |
| Inner shape fill | `#e4e4e4` |
| Emphasis shape fill | `#d4d4d4` |
| Border | `#a5a5a5` |
| Text and connector lines | `#404040` |

## Usage rules

- Do not co-brand with any other product name in public documentation,
  repository descriptions, or generated artefacts.
- Use only ASCII apostrophe (`'`) and double-quote (`"`); no smart
  quotes, no em/en dashes.
- Never include a company or maintainer name in generated file
  headers.
- Do not recolour the mark. The identity is grayscale on a transparent
  background and there is no coloured variant.
- Do not hand-edit a derived asset. Change `assets/brand/logo.png` and
  re-run `scripts/build_brand_assets.py`.
