# Cylinder Text Deboss

A Fusion add-in that puts text on a cylinder: sunk in, raised, outlined, in a
recessed panel or on a raised plate, with any Google font. It works in two
ways:

- **Smooth surface**: one NURBS surface body that sits flush with the
  cylinder at its edges and curves smoothly the whole way through.
- **Sharp, into the body**: cut into (or added to) the body itself, with
  exact sharp edges between separate faces, and real Fusion fillets if you
  want the edges rounded. On a surface body, which has no material to cut,
  the text goes into a new surface body made from it (see below).

## Install

Windows (PowerShell):

```powershell
irm https://raw.githubusercontent.com/vystal/CylinderTextDeboss/main/install.ps1 | iex
```

macOS (Terminal):

```sh
curl -fsSL https://raw.githubusercontent.com/vystal/CylinderTextDeboss/main/install.sh | sh
```

Either one downloads the latest version into Fusion's add-ins folder,
replacing any copy already there, so run it again to update. Then:

1. In Fusion: **Utilities > Add-Ins** (Shift+S), select
   *CylinderTextDeboss* and click **Run** (tick *Run on Startup* if you want
   it every session). Restart Fusion first if it was running an older copy.
2. **Solid > Create > Cylinder Text** (also under Surface > Create) opens the
   palette.

To run it from a clone instead, use the **+** next to *My Add-Ins* >
*Script or add-in from device*, and choose the folder.

## The palette

At the top, **Pick face / body** chooses the cylinder (what's selected if it
will do, otherwise it asks). Angle 0 is the side facing you when you pick.
Below that, a chip for each **text block**: **+ Add text** adds another
with its own settings and position, and clicking a chip edits that one.

The footer has **Live preview**, **Whole body in preview** (untick it to
see just the text area, so you can see how deep it goes), **Replace
previous** (regenerating swaps what the last Generate made rather than
adding to it) and **Generate**. If you hide the body yourself, the preview
respects that and shows just the text area too.

### Text

- The text (Enter for more lines).
- **Font**: all 1,800-odd Google Fonts families, listed most popular first,
  each shown in its own font. Type to search, or use the kind filter (sans,
  serif, display, script, mono). A font installed on this computer works too:
  type its name and press Enter. The list comes from `palette/google-fonts.js`;
  `python tools/update_fonts.py` refreshes it.
- Weight, text height (a capital H), letter spacing, line spacing, alignment.

### Shape

- **Edges**: *Smooth surface* or *Sharp, into the body*.
- **Style**:
  - *Letters sunk in*
  - *Letters raised*
  - *Outline grooves*: one or more grooves along the letter outlines,
    inside, on or outside them, with a width and gap
  - *Halo groove*: a groove round the letters a set gap away; the letters
    stay at the surface
  - *Background sunk*: the panel area sinks and the letters stand at the
    original surface
  - *Raised plate*: a plate stands up from the surface with the letters sunk
    into it
- **Profile**: every size is a share of each stroke (or groove), from its
  edge to its middle, so it looks the same on thin and thick strokes.
  - **Wall width**: how far in the wall reaches the floor (100% = a V).
  - Smooth only: **rim and floor roundness and shape**.
  - Sharp only: **top edge fillet** and **floor fillet**, real fillets
    added by Fusion after the cut. If Fusion can't fit them at that size
    they are tried again smaller, and the status line says so.
  - **Dish ↔ ridge**, **ridge width**, **ridge sharpness**: a ridge along
    the middle of each stroke (V becomes W) or a dish (U).
  - **Thin strokes shallower**: 100 makes depth follow stroke width, as a
    V-bit cuts.
  - **Grow letters**, **smoothing**, **resolution** (Auto picks enough
    points across a typical stroke, within the point budget).
  - Presets: Rounded, Soft pillow, Crisp, Flat floor, Paint fill (flat
    floor, steep walls), U-groove, Chisel, V-carve, W-carve, Inline.
- The cross-section shows a typical stroke as it will be made.

### Extras

- **Panel** (for the background and plate styles): rounded rectangle,
  rectangle, capsule, oval or *hug the text*; padding, corner radius, edge
  width, plate height, and an optional raised **lip** round it.
- **Drop shadow**: a shallow cut beside the letters, offset right and down,
  so they look lifted (not with the panel styles).
- **Texture** on the letter floors or tops, or on the background or plate:
  stipple, crosshatch, diamond knurl, dimples or ribs, with pitch, depth and
  angle.
- **Depth variation**: fade from a start to an end share along the text, and
  a share per line (e.g. `100, 70`).

### Place

Direction (lengthways or around), angle round the axis, offset along it,
flip; and the height map as shaded relief with its size, point count and how
much of the cylinder it covers.

## How it works

- **The height map** (`palette/index.html`): the text is drawn on a canvas.
  Exact distance transforms give each pixel its distance in from the outline
  and the half-width of the stroke it's in (from the strokes' centre lines,
  with the branches into corners dropped). The style turns those into
  offsets in mm (panels, grooves, shadow and textures are worked out per
  pixel the same way), smooths them a little, and samples them onto a grid
  of control points.
- **Smooth surface**: one offset field. `wrap.py` places each control point
  on the cylinder, pushed in or out by its offset, and builds one bicubic
  NURBS surface, cut into tiles so Fusion builds it fast. The outer three
  rings of control points are held on the cylinder, so the surface meets it
  with matching position, tangent and curvature.
- **Sharp, into the body**: each part of the style is a *layer*, a cut or a
  raise, with a *wall* surface that carries on straight past its ends: above
  the surface and below the floor for a cut. For each layer, `wrap.slab`
  closes the wall surface into a solid with a cylinder beyond it and four
  flat sides. That solid is intersected with a ring between the floor (or
  top) and the surface, giving exactly the material to take away (or add).
  Fusion works out where the walls cross the surface and the floor, which is
  what makes those edges sharp. All the tools go into one base feature and
  are combined with the body: raises joined, then cuts. A textured or varying
  floor is a second surface closed the same way. The tools are built once
  with the block at rest and turned and slid into place, so moving text is
  quick. Raises start exactly at the surface: one reaching in under it made
  Fusion's Join throw out spikes and fins at sharp corners, like the apex of
  an A.
- **Sharp, on a surface body**: the same tools are applied to a thin
  temporary solid under each text area. Its own sides and bottom are then
  removed, the matching patch is cut out of your surface, and the two are
  stitched into a new surface body. That body has the same sharp walls and
  floors. Your surface is hidden, not changed, and **Replace previous**
  deletes the new body and shows yours again.
- **Sharp, on a body in a linked component** (one inserted from another
  design): its bodies can't be changed from this design. The result is made
  as a new body, "<name> with text", in this design, and the original is
  hidden in the same way. A body in an ordinary sub-component is cut
  directly, like one in the root.
- **The preview** hides the body and draws, in the body's own appearance,
  what you'll get. For Sharp, that's the exact result of the cut, worked out
  in memory. With **Whole body in preview** off, or the body hidden by you,
  only the text area is drawn: the result trimmed to the region over the
  text. Sharp previews draw the edges as black lines, as Fusion does. For Smooth, it's a copy of the body with a shallow pocket under
  the text, and the surface over it. Both are drawn as meshes made here:
  drawing the body directly took Fusion up to 27 s for intricate cuts. The
  body is shown again when the preview stops (unticked, Generate, palette
  closed, add-in stopped).

## Speed

Measured for "Ag" at 5 mm on a 12 mm barrel:

| | First preview | Moving it | Generate |
| --- | --- | --- | --- |
| Sharp, letters sunk | 1.6–2.3 s | 0.1–0.2 s | 0.3–1.4 s |
| Sharp, raised + shadow, panel + lip, plate | 4–6 s | 0.3–0.5 s | 1–2.6 s |
| Sharp, double outline | 5.3 s | 1.6 s | 5.5 s |
| Sharp, knurled floor | 10.7 s | 0.2 s | 1.1 s |
| Smooth, any style | 0.4–0.7 s | 0.4–0.7 s | 1.3–1.7 s |

- The height maps are cached: changing only angle, offset, direction or
  flip reuses them, and Fusion isn't sent them again.
- One request at a time: while one is running, further changes collapse
  into a single update with the newest settings.
- Up to 200,000 control points per block. Sharp mode uses half as many
  (its walls are straight), which with seams placed between letters leaves
  most letters' walls in one or two faces (7 wall faces for "Ag", where a
  uniform tiling left 60).

## Things worth knowing

- **Fusion's "high accuracy" volume is off for these bodies.** Its "very
  high accuracy" figure and its plain volume agree with an independent check
  (sampling points inside), to within 1%. The "high accuracy" figure was
  20% out on a sharp-edged cut. The geometry is right.
- **Fillets** depend on the space available. Small radii work, but a radius
  too big for the narrowest gap between walls can't be made, and is then
  tried smaller. On raised letters, Fusion often only manages a quarter of
  the asked-for floor fillet. On a surface body, a fillet can leave a
  pinhole a few hundredths of a mm across where strokes meet (the bowl of
  a "g").
- **Sharp corners can upset Fusion's Booleans.** Where a wall meets the
  surface at a very sharp corner, such as the apex of an "A", Fusion has
  once or twice made a smear or fin. Whether it does depends on exactly
  where the text sits, so moving the text a little changes it. In testing,
  a small fillet cleared it.
- **Seams between faces**: walls longer than the tiles still have a few, as
  thin lines. The shape is continuous across them.
- **Smooth mode's surface** sits inside the body, so hide the body to see it
  after Generate.
- **Changing the code**: Fusion keeps imported modules for the whole session,
  so restart Fusion after editing the Python. `index.html` reloads when the
  palette is reopened.
- `python tools/make_icons.py` redraws the toolbar icons in `resources/`.

## Verified against

Fusion 2705.1.30. The API calls were checked against that build's Python stubs
and run in it:

- **Smooth surface:**
  - Geometry: orientation, flip, angle, offset, bores, flushness, around and
    lengthways.
  - Tiling: the tiled surface is the same as one face.
- **Sharp engine:**
  - Rim edges come out at exactly the wall angle (70.0° for 70° walls).
  - Bores cut outward into the material.
  - Fillets are made.
  - Deleting the features gives the body back exactly.
  - The cut volume matches the height map (5.29 mm³ sampled against 5.31
    expected).
- **Every style end to end** (sunk, filleted, raised with shadow, double
  outline, halo, background with lip, plate, knurled floor, W-carve, two
  blocks, plate with lip, raised with fillets, and smooth), with real
  messages from the palette running in Fusion's own browser:
  - preview, preview after a move, Generate, Replace;
  - renders checked by eye.
- **The same styles on a surface body**:
  - whole-body and text-area previews;
  - Generate gives one stitched shell, open only at the cylinder's ends;
  - Replace shows the original again;
  - a body you've hidden stays hidden, and the preview shows just the text.
- **Fonts:** Google Fonts load in Fusion's palette browser, and the whole
  catalogue lists, filters and searches.
- **Control sensitivity:** every profile control changes the surface
  noticeably at its default size.

One thing the stubs don't say, found by testing: `NurbsSurface.create` takes
control points with V varying fastest (`index = i * countV + j`).

The interactive pick from the palette has **not** been clicked through yet.
It uses `ui.selectEntity` from the palette's event handler; the API docs only
rule that out inside command events.
