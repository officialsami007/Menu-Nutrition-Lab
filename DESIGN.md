# Design

The app is built for a live demo on a laptop or projector, so it favours big numbers, strong
contrast and flat colour blocks with thick black outlines (a neo-brutalist style).

## Colours

| Colour | Hex | Used for |
|---|---|---|
| Blue | `#5B8CFF` | Drinks, everywhere (blocks, bars, charts) |
| Yellow | `#FFD23F` | Food, everywhere |
| Pink | `#FF5FA2` | Buttons, the active tab, the caffeine block |
| Green | `#3DDC97` | The drinks vs food ratio, success messages |
| Cream | `#FFF6E5` | Page background |
| Ink | `#111111` | Text, outlines and shadows |

**Blue always means drinks and yellow always means food**, on every page and chart.

Traffic-light colours (green `#3DDC97`, orange `#FF8A3D`, red `#FF4D4D`) mean only one thing:
how much of an adult's daily intake an item uses. Up to 10% is low, up to 25% is medium, and above that is high.
Protein and fiber are never coloured, because more of them isn't a bad thing.

## Type

One font, **Archivo**: weight 900 for big numbers and headings, 500 for body text.
Numbers use equal-width digits so columns line up.

## Shapes and depth

- 3px black outlines on cards, 2px on buttons and chips.
- Hard shadows with no blur: 6px on cards, 3px on buttons.
- Buttons sink into their shadow when pressed.
- Rounded corners: 16px on cards, 10px on buttons.

## Motion

Short and purposeful: 120–260 ms, with numbers counting up and bars growing once when a page
loads. Frequent actions like typing in a filter or sorting the table aren't animated. Animations
switch off for anyone who has "reduce motion" turned on.
