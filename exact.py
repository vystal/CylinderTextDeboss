"""Exact mode: the letters' real outlines, wrapped onto the cylinder by
Fusion's own Emboss feature.

The palette lays the text out from the font file and sends its outlines as
closed runs of cubic Béziers (mm, centred on the ink, y up). Here they are
drawn on a plane touching the cylinder at the text's centre, one spline per
contour, and each region of the sketch that is ink (or background, or
plate) is embossed on its own: Fusion's API refuses an Emboss of more than
one profile on a curved face. Emboss wraps by arc length, as the other
modes do, so the text lands where the preview shows it.
"""

import base64
import math
import os
import re
import tempfile
import urllib.parse
import urllib.request

import adsk.core
import adsk.fusion

from . import wrap

FONT_CACHE = os.path.join(tempfile.gettempdir(), "CylinderTextDeboss fonts")


# ---------------------------------------------------------------------------
# Fonts
# ---------------------------------------------------------------------------

def font_file(family, weight):
    """The bytes of a Google font as a TTF file, from Google's own servers,
    kept on disk after the first time. Asks for the weight and, if the
    family doesn't come in it, for the family's default."""
    os.makedirs(FONT_CACHE, exist_ok=True)
    safe = re.sub(r"[^A-Za-z0-9]+", "_", family)
    for query, tag in (("%s:wght@%s" % (family, weight), weight), (family, "default")):
        path = os.path.join(FONT_CACHE, "%s-%s.ttf" % (safe, tag))
        if os.path.exists(path):
            with open(path, "rb") as handle:
                return handle.read()
        url = "https://fonts.googleapis.com/css2?family=" + urllib.parse.quote(query, safe=":@")
        try:
            # Asked without a browser's name, Google answers with TTF files.
            css = urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "Python"}),
                                         timeout=20).read().decode("utf-8")
        except Exception:
            continue
        found = re.findall(r"url\((https://[^)]+)\)", css)
        if not found:
            continue
        data = urllib.request.urlopen(found[0], timeout=30).read()
        with open(path, "wb") as handle:
            handle.write(data)
        return data
    raise RuntimeError('"%s" isn\'t on Google Fonts (or couldn\'t be fetched). Exact mode reads the '
                       "letter shapes from Google's font files, so it needs a Google font." % family)


def font_reply(data):
    return {"ok": True, "data": base64.b64encode(font_file(data["family"], data["weight"])).decode("ascii")}


# ---------------------------------------------------------------------------
# Outlines
# ---------------------------------------------------------------------------

def _points(contour):
    """A contour from the palette as (x, y) points in mm: a start point, then
    three per cubic Bézier."""
    return [(contour[k], contour[k + 1]) for k in range(0, len(contour), 2)]


def _bezier(a, b, c, d, t):
    u = 1 - t
    return (u * u * u * a[0] + 3 * u * u * t * b[0] + 3 * u * t * t * c[0] + t * t * t * d[0],
            u * u * u * a[1] + 3 * u * u * t * b[1] + 3 * u * t * t * c[1] + t * t * t * d[1])


def _polyline(points, steps=10):
    out = [points[0]]
    for k in range(0, len(points) - 1, 3):
        a, b, c, d = points[k:k + 4]
        out += [_bezier(a, b, c, d, s / steps) for s in range(1, steps + 1)]
    return out


def _winding(point, polys):
    """Non-zero winding number of a point against closed polylines."""
    x, y = point
    w = 0
    for poly in polys:
        for (x0, y0), (x1, y1) in zip(poly, poly[1:]):
            if y0 <= y < y1 or y1 <= y < y0:
                if x0 + (y - y0) * (x1 - x0) / (y1 - y0) > x:
                    w += 1 if y1 > y0 else -1
    return w


def _interior_point(profile):
    """A point inside a sketch profile, in sketch space: the middle of the
    widest span across it on one of a few horizontal lines."""
    segments = []
    for loop in profile.profileLoops:
        for curve in loop.profileCurves:
            evaluator = curve.geometry.evaluator
            _, start, end = evaluator.getParameterExtents()
            _, points = evaluator.getStrokes(start, end, 0.0002)
            segments += [((p.x, p.y), (q.x, q.y)) for p, q in zip(points, points[1:])]
    box = profile.boundingBox
    best = None
    for share in (0.5, 0.37, 0.63, 0.25, 0.75, 0.13, 0.87):
        y = box.minPoint.y + share * (box.maxPoint.y - box.minPoint.y)
        xs = sorted(x0 + (y - y0) * (x1 - x0) / (y1 - y0) for (x0, y0), (x1, y1) in segments
                    if y0 <= y < y1 or y1 <= y < y0)
        for k in range(0, len(xs) - 1, 2):
            if best is None or xs[k + 1] - xs[k] > best[0]:
                best = (xs[k + 1] - xs[k], ((xs[k] + xs[k + 1]) / 2, y))
        if best and best[0] > 1e-4:
            return best[1]
    return best[1] if best else None


class _Patch:
    """Where a block's text sits: maps its mm (reading direction, up) to the
    plane touching the cylinder at its centre, to the cylinder itself (as
    Emboss wraps it, by arc length), and back."""

    def __init__(self, frame, place, radius=None):
        self.frame, self.place = frame, place
        self.radius = frame["radius"] if radius is None else radius
        self.cos_q, self.sin_q = place["turn"]
        self.x, self.right, self.up = place["x"], place["right"], place["up"]
        self.axis_point = place["centre"]
        self.touch = wrap._add(self.axis_point, wrap._scale(self.x, self.radius))

    def _arc_along(self, a, r):
        a, r = a / 10, r / 10
        return self.cos_q * a - self.sin_q * r, self.sin_q * a + self.cos_q * r

    def on_plane(self, a, r):
        arc, along = self._arc_along(a, r)
        return wrap._add(wrap._add(self.touch, wrap._scale(self.right, arc)), wrap._scale(self.up, along))

    def on_cylinder(self, a, r, radius=None):
        arc, along = self._arc_along(a, r)
        k = self.radius if radius is None else radius
        theta = arc / self.radius
        out = wrap._add(wrap._scale(self.x, math.cos(theta)), wrap._scale(self.right, math.sin(theta)))
        return wrap._add(wrap._add(self.axis_point, wrap._scale(out, k)), wrap._scale(self.up, along))

    def wrapped(self, model_point):
        """A point on the plane (model space) wrapped onto the cylinder."""
        d = wrap._sub(model_point, self.touch)
        arc, along = wrap._dot(d, self.right), wrap._dot(d, self.up)
        theta = arc / self.radius
        out = wrap._add(wrap._scale(self.x, math.cos(theta)), wrap._scale(self.right, math.sin(theta)))
        return wrap._add(wrap._add(self.axis_point, wrap._scale(out, self.radius)), wrap._scale(self.up, along))


# ---------------------------------------------------------------------------
# Preview: the outlines drawn on the cylinder
# ---------------------------------------------------------------------------

def preview(group, frame, place, shape, levels):
    """Draw the outlines wrapped onto the cylinder, as lines: black at the
    surface, grey at each of `levels` (mm out from the surface, negative in),
    where the floor or top will be."""
    patch = _Patch(frame, place)
    sign = frame["sign"]
    runs = [_polyline(_points(c), 8) for c in shape["contours"]]
    if shape.get("panel"):
        runs.append(_polyline(_points(shape["panel"]), 16))
    for level, colour in [(0.0, (0, 0, 0))] + [(v, (120, 120, 120)) for v in levels if v]:
        radius = frame["radius"] + sign * (level / 10 + 0.0004)
        coordinates, lengths = [], []
        for run in runs:
            for a, r in run:
                coordinates += patch.on_cylinder(a, r, radius)
            lengths.append(len(run))
        lines = group.addLines(adsk.fusion.CustomGraphicsCoordinates.create(coordinates), [], True, lengths)
        lines.color = adsk.fusion.CustomGraphicsSolidColorEffect.create(adsk.core.Color.create(*colour, 255))
        lines.weight = 2 if level == 0 else 1
        lines.depthPriority = 1
        lines.isSelectable = False


# ---------------------------------------------------------------------------
# Generate: sketch and emboss
# ---------------------------------------------------------------------------

def _plane(design, patch):
    """A construction plane touching the cylinder at the text's centre."""
    root = design.rootComponent
    normal = wrap._scale(patch.x, patch.frame["sign"])
    plane = adsk.core.Plane.create(wrap._point(patch.touch), wrap._vector(normal))
    made = []
    if design.designType == adsk.fusion.DesignTypes.ParametricDesignType:
        feature = root.features.baseFeatures.add()
        feature.startEdit()
        plane_input = root.constructionPlanes.createInput()
        plane_input.setByPlane(plane)
        plane_input.targetBaseFeature = feature
        root.constructionPlanes.add(plane_input)
        feature.finishEdit()
        made.append(feature)
        return feature.constructionPlanes[0], made
    plane_input = root.constructionPlanes.createInput()
    plane_input.setByPlane(plane)
    construction = root.constructionPlanes.add(plane_input)
    made.append(construction)
    return construction, made


def _sketch(design, construction, patch, contours, name):
    """One fixed spline per contour on the plane. Returns the sketch and the
    contours as polylines in sketch space, for telling ink from holes."""
    sketch = design.rootComponent.sketches.add(construction)
    sketch.name = name
    to_sketch = sketch.transform.copy()
    to_sketch.invert()

    def sketch_point(a, r):
        p = wrap._point(patch.on_plane(a, r))
        p.transformBy(to_sketch)
        return adsk.core.Point3D.create(p.x, p.y, 0.0)

    polys = []
    sketch.isComputeDeferred = True
    for contour in contours:
        points = _points(contour)
        count = (len(points) - 1) // 3
        knots = ([0.0] * 4 + [float(k) for k in range(1, count) for _ in range(3)] + [float(count)] * 4)
        curve = adsk.core.NurbsCurve3D.createNonRational([sketch_point(a, r) for a, r in points],
                                                         3, knots, False)
        sketch.sketchCurves.sketchFixedSplines.addByNurbsCurve(curve)
        polys.append([(q.x, q.y) for q in (sketch_point(a, r) for a, r in _polyline(points, 8))])
    sketch.isComputeDeferred = False
    sketch.isVisible = False
    return sketch, polys


def _profiles(sketch, keep):
    """The sketch's profiles whose inside passes keep(point in sketch space)."""
    chosen = []
    for k in range(sketch.profiles.count):
        profile = sketch.profiles.item(k)
        point = _interior_point(profile)
        if point is not None and keep(point):
            chosen.append((profile, point))
    return chosen


def _emboss(design, body, sketch, patch, profiles, depth_cm, radius):
    """One Emboss per profile, onto the face of the body under it (found
    afresh each time, since each emboss changes the faces). Returns the
    features."""
    root = design.rootComponent
    to_model = sketch.transform
    sign = patch.frame["sign"]
    made = []
    for profile, (sx, sy) in profiles:
        p = adsk.core.Point3D.create(sx, sy, 0.0)
        p.transformBy(to_model)
        on_face = patch.wrapped((p.x, p.y, p.z))
        # Onto the cylinder of the right radius (the plate's top, for letters
        # sunk into a plate).
        d = wrap._sub(on_face, patch.axis_point)
        along = wrap._dot(d, patch.up)
        out = wrap._unit(wrap._sub(d, wrap._scale(patch.up, along)))
        target = wrap._add(wrap._add(patch.axis_point, wrap._scale(out, radius)), wrap._scale(patch.up, along))
        found = root.findBRepUsingPoint(wrap._point(target), adsk.fusion.BRepEntityTypes.BRepFaceEntityType,
                                        0.001, True)
        faces = [f for f in found if f.body == body
                 and f.geometry.surfaceType == adsk.core.SurfaceTypes.CylinderSurfaceType
                 and abs(f.geometry.radius - radius) < 1e-6]
        if not faces:
            raise RuntimeError("Part of the text isn't over the cylinder (it runs off an end, "
                               "or onto something else).")
        emboss_input = root.features.embossFeatures.createInput(
            [profile], faces[:1], adsk.core.ValueInput.createByReal(depth_cm))
        feature = root.features.embossFeatures.add(emboss_input)
        if feature.healthState != adsk.fusion.FeatureHealthStates.HealthyFeatureHealthState:
            message = feature.errorOrWarningMessage or "Fusion couldn't emboss part of the text."
            feature.deleteMe()
            raise RuntimeError(message.split("\n")[0])
        made.append(feature)
    return made


def generate(design, body, frame, place, shape, text):
    """Emboss one block's text into (or onto) the body. Returns the features
    made, in the order made.

    shape  the outlines, with the block's style, depthMm and plateMm"""
    style = shape["style"]
    depth = shape["depthMm"] / 10
    radius = frame["radius"]
    sign = frame["sign"]
    patch = _Patch(frame, place)
    name = "Text " + text.split("\n")[0][:30]
    made = []
    construction, planes = _plane(design, patch)
    made += planes
    letters = shape["contours"]

    if style in ("sunk", "raised"):
        sketch, polys = _sketch(design, construction, patch, letters, name)
        made.append(sketch)
        ink = _profiles(sketch, lambda p: _winding(p, polys) != 0)
        made += _emboss(design, body, sketch, patch, ink, -depth if style == "sunk" else depth, radius)
    elif style == "background":
        sketch, polys = _sketch(design, construction, patch, letters + [shape["panel"]], name)
        made.append(sketch)
        panel, text = polys[-1:], polys[:-1]
        back = _profiles(sketch, lambda p: _winding(p, panel) != 0 and _winding(p, text) == 0)
        made += _emboss(design, body, sketch, patch, back, -depth, radius)
    elif style == "plate":
        plate = shape["plateMm"] / 10
        sketch, polys = _sketch(design, construction, patch, [shape["panel"]], name + " plate")
        made.append(sketch)
        made += _emboss(design, body, sketch, patch, _profiles(sketch, lambda p: True), plate, radius)
        # The letters go into the plate's top, from a plane touching it.
        top = _Patch(frame, place, radius + sign * plate)
        construction, planes = _plane(design, top)
        made += planes
        sketch, polys = _sketch(design, construction, top, letters, name)
        made.append(sketch)
        ink = _profiles(sketch, lambda p: _winding(p, polys) != 0)
        made += _emboss(design, body, sketch, top, ink, -depth, radius + sign * plate)
    else:
        raise RuntimeError("Exact mode doesn't do the %s style." % style)
    return made


def levels(shape):
    """Where the preview draws the grey outlines: the floor or top, mm out."""
    style, depth = shape["style"], shape["depthMm"]
    if style == "raised":
        return [depth]
    if style == "plate":
        return [shape["plateMm"], shape["plateMm"] - depth]
    return [-depth]
