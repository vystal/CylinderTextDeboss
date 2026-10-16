// Exact letter outlines, for Exact mode.
//
// The text is laid out the way the palette lays it out for the height maps:
// a capital H as tall as the text height, the letter spacing after every
// letter, lines a line-spacing apart and aligned on their advance widths.
// Overlapping pieces are merged into clean outlines, since many fonts build
// letters from overlapping pieces and Fusion won't emboss regions made by
// crossing curves; so are letters that touch.
//
// Needs opentype.js (to read the font) and paper.js (to merge the outlines).

/* global opentype, paper */

// How far a curve's zero-length handle is moved toward its other one, as a
// share of the distance (see textOutlines).
const NUDGE = 0.002;

// opentype.js font from the bytes of a TTF or OTF file.
function parseFont(bytes) {
  return opentype.parse(bytes);
}

let paperReady = false;
function paperScope() {
  if (!paperReady) {
    paper.setup(new paper.Size(1, 1));
    paperReady = true;
  }
  return paper;
}

// One glyph's contours as paper.js paths, in mm, y up.
function glyphContours(glyph, x, y, scale) {
  const P = paperScope();
  const contours = [];
  let current = null;
  const at = (gx, gy) => new P.Point(x + gx * scale, y + gy * scale);
  for (const c of glyph.path.commands) {
    if (c.type === "M") {
      current = new P.Path({ insert: false });
      current.moveTo(at(c.x, c.y));
      contours.push(current);
    } else if (c.type === "L") {
      current.lineTo(at(c.x, c.y));
    } else if (c.type === "Q") {
      current.quadraticCurveTo(at(c.x1, c.y1), at(c.x, c.y));
    } else if (c.type === "C") {
      current.cubicCurveTo(at(c.x1, c.y1), at(c.x2, c.y2), at(c.x, c.y));
    } else if (c.type === "Z") {
      current.closePath();
    }
  }
  return contours.filter(c => c.segments.length > 1 && Math.abs(c.area) > 1e-12);
}

// A glyph's filled region, with overlapping pieces merged. Fonts fill by the
// non-zero rule. A contour may cross itself (some fonts make the counter of
// a 6 or a B that way), so each is first resolved on its own into a region.
// Then, across contours, outlines run one way and holes the other, the
// largest contour being an outline: each outline loses the holes inside it,
// and the pieces are united. Uniting (rather than resolving the crossings
// of the whole glyph at once) also merges edges that lie along each other,
// as where a letter is built of overlapping strokes.
function glyphRegion(contours) {
  if (!contours.length) return null;
  const largest = contours.reduce((a, b) => Math.abs(b.area) > Math.abs(a.area) ? b : a);
  const outward = Math.sign(largest.area);
  const own = c => c.resolveCrossings().reorient(true, true);
  const outlines = contours.filter(c => Math.sign(c.area) === outward)
    .sort((a, b) => Math.abs(b.area) - Math.abs(a.area));
  const holes = contours.filter(c => Math.sign(c.area) !== outward);
  const used = new Set();
  let region = null;
  for (const outline of outlines) {
    let piece = own(outline);
    holes.forEach((hole, k) => {
      if (!used.has(k) && piece.contains(hole.interiorPoint)) {
        piece = piece.subtract(own(hole), { insert: false });
        used.add(k);
      }
    });
    region = region ? region.unite(piece, { insert: false }) : piece;
  }
  return region;
}

// The text's outlines: { contours, widthMm, heightMm }. Each contour is a
// closed run of cubic Béziers, flattened to [x0, y0, x1, y1, ...]: a start
// point, then three points per curve, ending back at the start. In mm,
// centred on the ink, y up.
function textOutlines(font, p) {
  paperScope();
  const H = font.charToGlyph("H").getBoundingBox();
  if (!(H.y2 > 0)) throw new Error("This font has no capital H to size the text by.");
  const scale = p.heightMm / H.y2;                       // mm per font unit
  const lineStep = p.heightMm * p.lineSpacing;
  const regions = [];
  p.text.split("\n").forEach((line, k) => {
    const glyphs = font.stringToGlyphs(line);
    const xs = [];
    let x = 0;
    glyphs.forEach((g, i) => {
      if (i > 0) x += font.getKerningValue(glyphs[i - 1], g) * scale;
      xs.push(x);
      x += g.advanceWidth * scale + p.letterMm;
    });
    const shift = p.align === "center" ? -x / 2 : p.align === "right" ? -x : 0;
    glyphs.forEach((g, i) => {
      const region = glyphRegion(glyphContours(g, xs[i] + shift, -k * lineStep, scale));
      if (region) regions.push(region);
    });
  });
  if (!regions.length) throw new Error("Type some text.");

  // Letters that touch or overlap become one region.
  const merged = [];
  for (let region of regions) {
    for (let k = merged.length - 1; k >= 0; k--) {
      if (merged[k].bounds.intersects(region.bounds) && merged[k].intersects(region)) {
        region = merged[k].unite(region, { insert: false });
        merged.splice(k, 1);
      }
    }
    merged.push(region);
  }
  const bounds = merged.map(r => r.bounds).reduce((a, b) => a.unite(b));
  const cx = bounds.center.x, cy = bounds.center.y;

  const contours = [];
  for (const path of merged.flatMap(r => r.children ? r.children : [r])) {
    const segs = path.segments;
    if (segs.length < 2) continue;
    const out = [segs[0].point.x - cx, segs[0].point.y - cy];
    for (let i = 0; i < segs.length; i++) {
      const a = segs[i], b = segs[(i + 1) % segs.length];
      const p0 = a.point, p3 = b.point;
      let c1 = p0.add(a.handleOut), c2 = p3.add(b.handleIn);
      // A control point on its end point leaves the curve with no direction
      // there, and Fusion then makes no profiles from it. Straight lines get
      // their thirds (the same line); a curve with one such handle gets it
      // moved a hair toward the other (well under a micrometre for a 1 mm
      // curve).
      const noOut = a.handleOut.isZero(), noIn = b.handleIn.isZero();
      if (noOut && noIn) {
        c1 = p0.add(p3.subtract(p0).divide(3));
        c2 = p0.add(p3.subtract(p0).multiply(2 / 3));
      } else if (noOut) {
        c1 = p0.add(c2.subtract(p0).multiply(NUDGE));
      } else if (noIn) {
        c2 = p3.add(c1.subtract(p3).multiply(NUDGE));
      }
      if (p0.getDistance(p3) < 1e-9 && c1.getDistance(p0) < 1e-9 && c2.getDistance(p0) < 1e-9) continue;
      out.push(c1.x - cx, c1.y - cy, c2.x - cx, c2.y - cy, p3.x - cx, p3.y - cy);
    }
    contours.push(out);
  }
  return { contours, widthMm: bounds.width, heightMm: bounds.height };
}

if (typeof module !== "undefined") module.exports = { parseFont, textOutlines };
