"""Geometry: wrap a height map onto a cylinder as a smooth NURBS surface.

Everything here is in Fusion's internal unit, centimetres. Vectors are plain
(x, y, z) tuples; Fusion objects are only made for the final geometry.

The control net is worked out once (control_points) and then either shown as
a quick preview mesh (preview_mesh) or built into a surface body
(build_body).
"""

import bisect
import math

import adsk.core
import adsk.fusion

DEGREE = 3

# Outer rings of control points held exactly on the cylinder. For a bicubic
# patch, three rings make the edge meet the cylinder with the same position,
# tangent and curvature, so it blends in rather than creasing.
PINNED_RINGS = 3


def cylinder_face(entity):
    """The cylindrical face to wrap onto: the entity itself, or a body's largest."""
    cylinder = adsk.core.SurfaceTypes.CylinderSurfaceType
    face = adsk.fusion.BRepFace.cast(entity)
    if face:
        return face if face.geometry.surfaceType == cylinder else None
    body = adsk.fusion.BRepBody.cast(entity)
    if body:
        faces = [f for f in body.faces if f.geometry.surfaceType == cylinder]
        return max(faces, key=lambda f: f.area) if faces else None
    return None


def cylinder_frame(face, eye):
    """Where the cylinder is and which way is which.

    mid    point on the axis level with the middle of the face
    zero   unit direction off the axis toward `eye`, so angle 0 faces the viewer
    sign   +1 for an outside face, -1 for a bore; the material is at -sign
    """
    cyl = face.geometry
    axis = _unit(_tuple(cyl.axis))
    origin = _tuple(cyl.origin)

    evaluator = face.evaluator
    box = evaluator.parametricRange()
    lo, hi = box.minPoint, box.maxPoint
    params = [adsk.core.Point2D.create(lo.x + (hi.x - lo.x) * a / 4,
                                       lo.y + (hi.y - lo.y) * b / 4)
              for a in range(5) for b in range(5)]
    _, points = evaluator.getPointsAtParameters(params)
    heights = [_dot(_sub(_tuple(p), origin), axis) for p in points]
    mid = _add(origin, _scale(axis, (min(heights) + max(heights)) / 2))

    centre = 12  # the middle of the 5 x 5 sample
    _, normal = evaluator.getNormalAtParameter(params[centre])
    radial = _off_axis(_sub(_tuple(points[centre]), mid), axis)
    sign = 1 if _dot(_tuple(normal), radial) > 0 else -1

    zero = _off_axis(_sub(_tuple(eye), mid), axis)
    if _dot(zero, zero) < 1e-12:  # looking straight down the axis
        zero = radial
    return {"mid": mid, "axis": axis, "zero": _unit(zero), "sign": sign,
            "radius": cyl.radius, "length": max(heights) - min(heights)}


def placement(frame, width, height, angle_deg=0.0, offset=0.0, flip=False,
              lengthways=False):
    """Where the text's patch goes: the viewer's frame at its centre, how the
    image is turned, and how far the patch spans around and along the axis.
    The arguments are as for control_points."""
    radius, sign = frame["radius"], frame["sign"]
    arc = height if lengthways else width
    if arc / radius >= 2 * math.pi:
        raise ValueError("The text would wrap more than once around the cylinder.")

    # The viewer's frame at the text centre: out of the face, right, and up
    # the axis. A positive angle is always toward the viewer's right.
    turn = math.radians(angle_deg)
    zero, up = frame["zero"], frame["axis"]
    x = _add(_scale(zero, math.cos(turn)),
             _scale(_cross(up, zero), sign * math.sin(turn)))
    right = _scale(_cross(up, x), sign)

    # Turn the image in the unrolled surface: a quarter turn anticlockwise
    # for lengthways, a half turn for flip. A turn keeps the u-v handedness,
    # so the surface normal still matches the face's.
    quarters = (1 if lengthways else 0) + (2 if flip else 0)
    return {"x": x, "right": right, "up": up,
            "centre": _add(frame["mid"], _scale(up, offset)),
            "turn": ((1, 0), (0, 1), (-1, 0), (0, -1))[quarters],
            "angle": arc / radius,                          # radians around
            "length": width if lengthways else height}      # along the axis


def control_points(frame, heights, nu, nv, width, height, depth,
                   angle_deg=0.0, offset=0.0, flip=False, lengthways=False,
                   pinned=True):
    """The control net on the cylinder: nu * nv (x, y, z) tuples, index i*nv + j.

    heights    nu * nv values from 0 to 1, row by row from the top-left of the
               text image (the way a canvas stores pixels)
    width      how long the image is in the reading direction
    height     how tall the image is across it
    depth      how far a height of 1 sinks into the material
    angle_deg  moves the text around the axis, positive to the viewer's right
    offset     moves the text along the axis from the middle of the face
    flip       turns the text upside down
    lengthways runs the text along the axis, reading toward +axis; otherwise
               it runs around the cylinder, reading to the viewer's right
    pinned     holds the outer rings on the cylinder; off for the surfaces
               that cut sharp edges, which must stay clear of it there
    """
    rings = PINNED_RINGS if pinned else 0
    if nu < 2 * PINNED_RINGS + 2 or nv < 2 * PINNED_RINGS + 2:
        raise ValueError("The grid is too coarse; use a smaller resolution.")
    if len(heights) != nu * nv:
        raise ValueError("Expected %d heights, got %d." % (nu * nv, len(heights)))
    place = placement(frame, width, height, angle_deg, offset, flip, lengthways)
    radius, sign = frame["radius"], frame["sign"]
    x, right, up = place["x"], place["right"], place["up"]
    cx, cy, cz = place["centre"]
    ux, uy, uz = up
    cos_q, sin_q = place["turn"]

    # Quarter turns put the arc wholly on i or wholly on j, so the direction
    # out of the axis only needs working out once per column or row.
    across = [(i / (nu - 1) - 0.5) * width for i in range(nu)]
    rise = [(j / (nv - 1) - 0.5) * height for j in range(nv)]
    outward = {}

    def out(arc):
        if arc not in outward:
            theta = arc / radius
            outward[arc] = _add(_scale(x, math.cos(theta)), _scale(right, math.sin(theta)))
        return outward[arc]

    sunk = sign * depth
    last_u, last_v = nu - rings, nv - rings
    points = []
    # Fusion wants NURBS control points with V varying fastest: i*nv + j.
    for i in range(nu):
        a = across[i]
        held_u = i < rings or i >= last_u
        for j in range(nv):
            r = rise[j]
            held = held_u or j < rings or j >= last_v
            h = 0.0 if held else heights[(nv - 1 - j) * nu + i]
            ox, oy, oz = out(cos_q * a - sin_q * r)       # arc toward viewer's right
            along = sin_q * a + cos_q * r                 # up the axis
            k = radius - sunk * h
            points.append((cx + ox * k + ux * along,
                           cy + oy * k + uy * along,
                           cz + oz * k + uz * along))
    return points


def build_body(points, nu, nv, tile_spans=None):
    """A transient surface body over the control net.

    The surface is cut into faces of at most tile_spans spans each way. Each
    cut knot is inserted until the spline passes through a row of control
    points there, so every face is a piece of exactly the same surface and
    neighbours meet with the same position, tangent and curvature. Fusion's
    time to build a face grows roughly with the square of its control
    points, so many small faces are far quicker than one big one.
    tile_spans=None makes a single face.
    """
    definition = adsk.fusion.BRepBodyDefinition.create()
    shell = definition.lumpDefinitions.add().shellDefinitions.add()
    _add_net(definition, shell, points, nu, nv, tile_spans)
    return _create(definition, "the surface")


def _create(definition, what):
    body = definition.createBody()
    if body is None:
        raise RuntimeError("Fusion could not build %s: %s"
                           % (what, "; ".join(definition.outcomeInfo)))
    return body


def _add_net(definition, shell, points, nu, nv, tile_spans, busy=None, reverse=False):
    """Add the control net's surface to `shell` as faces, cut into tiles as
    build_body describes. Returns its corner vertices, keyed (0, 0) to
    (1, 1) in (u, v), and the edges along each side in order: "v0" and "v1"
    run along u, "u0" and "u1" along v.

    busy     (columns, rows) flags, True where the surface shows in the
             result; tile edges are put elsewhere where they can be
    reverse  the faces point against u x v rather than along it
    """
    busy_u, busy_v = busy if busy else (None, None)
    grid = [points[i * nv:(i + 1) * nv] for i in range(nu)]
    columns = _split(grid, _clamped_knots(nu), _cuts(nu, tile_spans, busy_u))
    # tiles[a][b] = (grid[i][j], knots_u, knots_v) for tile column a, row b.
    tiles = []
    for sub, knots_u in columns:
        rows = _split(_transpose(sub), _clamped_knots(nv), _cuts(nv, tile_spans, busy_v))
        tiles.append([(_transpose(piece), knots_u, knots_v) for piece, knots_v in rows])
    count_u, count_v = len(tiles), len(tiles[0])
    vertices, edges = {}, {}

    def vertex(a, b, p):
        if (a, b) not in vertices:
            vertices[(a, b)] = definition.createVertexDefinition(_point(p))
        return vertices[(a, b)]

    # ("u", a, b) runs along u on the b-th row boundary, under tile column a;
    # ("v", a, b) runs along v on the a-th column boundary, beside tile row b.
    # A clamped piece's boundary is exactly the spline of its edge row of
    # control points, so the edges are built the same way and shared by the
    # faces either side.
    def edge(key):
        if key not in edges:
            kind, a, b = key
            if kind == "u":
                sub, knots, _ = tiles[a][min(b, count_v - 1)]
                row = [column[0 if b < count_v else -1] for column in sub]
                start, end = vertex(a, b, row[0]), vertex(a + 1, b, row[-1])
            else:
                sub, _, knots = tiles[min(a, count_u - 1)][b]
                row = sub[0 if a < count_u else -1]
                start, end = vertex(a, b, row[0]), vertex(a, b + 1, row[-1])
            curve = adsk.core.NurbsCurve3D.createNonRational(
                [_point(p) for p in row], DEGREE, knots, False)
            edges[key] = definition.createEdgeDefinitionByCurve(start, end, curve)
        return edges[key]

    open_ = adsk.core.NurbsSurfaceProperties.OpenNurbsSurface
    for a in range(count_u):
        for b in range(count_v):
            sub, knots_u, knots_v = tiles[a][b]
            surface = adsk.core.NurbsSurface.create(
                DEGREE, DEGREE, len(sub), len(sub[0]),
                [_point(p) for column in sub for p in column],
                knots_u, knots_v, [], open_, open_)
            # Anticlockwise in (u, v): along the bottom, up the right, back
            # along the top and down the left, the last two against their
            # curves' direction. A reversed face goes round the other way.
            loop_edges = [(("u", a, b), False), (("v", a + 1, b), False),
                          (("u", a, b + 1), True), (("v", a, b), True)]
            if reverse:
                loop_edges = [(key, not opposed) for key, opposed in reversed(loop_edges)]
            loop = shell.faceDefinitions.add(surface, reverse).loopDefinitions.add()
            for key, opposed in loop_edges:
                loop.bRepCoEdgeDefinitions.add(edge(key), opposed)

    corners = {(0, 0): vertex(0, 0, points[0]),
               (1, 0): vertex(count_u, 0, points[(nu - 1) * nv]),
               (0, 1): vertex(0, count_v, points[nv - 1]),
               (1, 1): vertex(count_u, count_v, points[nu * nv - 1])}
    sides = {"v0": [edge(("u", a, 0)) for a in range(count_u)],
             "v1": [edge(("u", a, count_v)) for a in range(count_u)],
             "u0": [edge(("v", 0, b)) for b in range(count_v)],
             "u1": [edge(("v", count_u, b)) for b in range(count_v)]}
    return corners, sides


def slab(points, nu, nv, frame, far_radius, air_side, tile_spans=None, busy=None):
    """A closed temporary solid: everything between the control net's surface
    and a cylinder of radius far_radius about the same axis, on the side the
    surface faces (air_side) or the side behind it.

    The net must be unpinned, with its edges clear of the region the solid
    is used to cut. Each of its four edges keeps to one angle round the axis
    or one distance along it, so the sides are flat: a plane through the
    axis, or one square to it.
    """
    definition = adsk.fusion.BRepBodyDefinition.create()
    shell = definition.lumpDefinitions.add().shellDefinitions.add()
    # On the air side the surface is the solid's floor, so it faces inward.
    corners, sides = _add_net(definition, shell, points, nu, nv, tile_spans, busy,
                              reverse=air_side)
    up, mid = frame["axis"], frame["mid"]

    def foot(p):
        return _add(mid, _scale(up, _dot(_sub(p, mid), up)))

    def far(p):
        f = foot(p)
        return _add(f, _scale(_unit(_sub(p, f)), far_radius))

    at = {(0, 0): points[0], (1, 0): points[(nu - 1) * nv],
          (0, 1): points[nv - 1], (1, 1): points[nu * nv - 1]}
    far_vertex = {k: definition.createVertexDefinition(_point(far(p))) for k, p in at.items()}
    risers = {k: definition.createEdgeDefinitionByCurve(
        corners[k], far_vertex[k], adsk.core.Line3D.create(_point(p), _point(far(p))))
        for k, p in at.items()}
    inside = points[(nu // 2) * nv + nv // 2]

    def face(surface, pieces, polygon, outward):
        """A face whose loop runs anticlockwise seen from outside the solid."""
        if _dot(_newell(polygon), outward) < 0:
            pieces = [(e, not opposed) for e, opposed in reversed(pieces)]
        loop = shell.faceDefinitions.add(surface[0], surface[1]).loopDefinitions.add()
        for e, opposed in pieces:
            loop.bRepCoEdgeDefinitions.add(e, opposed)

    # The four sides: (first corner, second corner, a control point midway).
    far_edges = {}
    for name, (a, b, middle) in {
            "v0": ((0, 0), (1, 0), points[(nu // 2) * nv]),
            "v1": ((0, 1), (1, 1), points[(nu // 2) * nv + nv - 1]),
            "u0": ((0, 0), (0, 1), points[nv // 2]),
            "u1": ((1, 0), (1, 1), points[(nu - 1) * nv + nv // 2])}.items():
        pa, pb = at[a], at[b]
        if abs(_dot(_sub(pb, pa), up)) > 1e-9:          # runs along the axis
            curve = adsk.core.Line3D.create(_point(far(pa)), _point(far(pb)))
            normal = _unit(_cross(up, _off_axis(_sub(pa, mid), up)))
        else:                                             # runs round it
            curve = adsk.core.Arc3D.createByThreePoints(
                _point(far(pa)), _point(far(middle)), _point(far(pb)))
            normal = up
        if _dot(normal, _sub(inside, pa)) > 0:
            normal = _scale(normal, -1)
        far_edges[name] = definition.createEdgeDefinitionByCurve(far_vertex[a], far_vertex[b], curve)
        pieces = ([(e, False) for e in sides[name]]
                  + [(risers[b], False), (far_edges[name], True), (risers[a], True)])
        face((adsk.core.Plane.create(_point(pa), _vector(normal)), False),
             pieces, [pa, pb, far(pb), far(pa)], normal)

    # The far side, part of a cylinder.
    outward = _unit(_sub(far(inside), inside))
    radial = _unit(_sub(inside, foot(inside)))
    pieces = [(far_edges["v0"], False), (far_edges["u1"], False),
              (far_edges["v1"], True), (far_edges["u0"], True)]
    cylinder = adsk.core.Cylinder.create(_point(mid), _vector(up), far_radius)
    face((cylinder, _dot(outward, radial) < 0), pieces,
         [far(at[k]) for k in ((0, 0), (1, 0), (1, 1), (0, 1))], outward)
    return _create(definition, "a cutting solid")


def ring(frame, place, r1, r2):
    """A temporary tube about the cylinder's axis between radii r1 and r2,
    reaching well past the patch both ways."""
    temp = adsk.fusion.TemporaryBRepManager.get()
    inner, outer = min(r1, r2), max(r1, r2)
    reach = place["length"] / 2 + 1
    centre, up = place["centre"], place["up"]

    def end(k):
        return _point(_add(centre, _scale(up, k)))

    tube = temp.createCylinderOrCone(end(-reach), outer, end(reach), outer)
    if inner > 1e-6:
        core = temp.createCylinderOrCone(end(-reach - 1), inner, end(reach + 1), inner)
        temp.booleanOperation(tube, core, adsk.fusion.BooleanTypes.DifferenceBooleanType)
    return tube


def layer_tool(frame, place, wall, nu, nv, cut, low, high, cap=None, tile_spans=None, busy=None,
               clear=None):
    """The exact solid one layer takes out of the body (cut) or adds to it.

    Offsets here are in cm out from the cylinder, toward the air.

    wall       control net of the wall surface. Where the layer does nothing it
               must lie outside low..high: above it for a cut, below for a raise.
    low, high  a cut takes material from the wall up to `high` (just above the
               surface) and down no further than `low` (the floor); a raise
               adds it from `low` (just under the surface) up to `high` (the top)
    cap        optional control net of a floor (cut) or top (raise) that isn't
               flat, used instead of `low` or `high`
    clear      the furthest the wall goes on the far side of the work (above
               `high` for a cut, below `low` for a raise); the solid's far side
               goes past it
    The walls meet the surface, floor and top at an angle, so Fusion works
    out exactly where: those edges are sharp, between separate faces.
    """
    temp = adsk.fusion.TemporaryBRepManager.get()
    radius, sign = frame["radius"], frame["sign"]
    if clear is None:
        clear = high if cut else low
    reach = (max(high, clear) + 0.05) if cut else (min(low, clear) - 0.05)
    far = radius + sign * reach
    solid = slab(wall, nu, nv, frame, far, cut, tile_spans, busy)
    band = ring(frame, place, radius + sign * low, radius + sign * high)
    if not temp.booleanOperation(solid, band, adsk.fusion.BooleanTypes.IntersectionBooleanType):
        raise RuntimeError("Fusion could not trim a layer to its depth.")
    if cap is not None:
        under = slab(cap, nu, nv, frame, far, cut, tile_spans, busy)
        if not temp.booleanOperation(solid, under, adsk.fusion.BooleanTypes.IntersectionBooleanType):
            raise RuntimeError("Fusion could not trim a layer to its floor.")
    return solid


def apply_tools(design, target, tools, name):
    """Combine temporary tool solids with the target body: (cut, solid) pairs,
    the raises first, then the cuts. Returns the features made, last first,
    so they can be deleted to take it all back out."""
    root = design.rootComponent
    parametric = design.designType == adsk.fusion.DesignTypes.ParametricDesignType
    made = []
    feature = None
    if parametric:
        feature = root.features.baseFeatures.add()
        feature.startEdit()
    added = []
    for cut, solid in tools:
        body = root.bRepBodies.add(solid, feature) if parametric else root.bRepBodies.add(solid)
        added.append((cut, body))
    if parametric:
        feature.finishEdit()
        feature.name = name + " tools"
        made.append(feature)
        # The bodies' references don't survive finishing the edit.
        new = [root.bRepBodies.item(root.bRepBodies.count - len(added) + k) for k in range(len(added))]
        added = [(cut, body) for (cut, _), body in zip(added, new)]
    try:
        for want_cut in (False, True):
            tools_now = adsk.core.ObjectCollection.create()
            for cut, body in added:
                if cut == want_cut:
                    tools_now.add(body)
            if tools_now.count == 0:
                continue
            combine = root.features.combineFeatures.createInput(target, tools_now)
            combine.operation = (adsk.fusion.FeatureOperations.CutFeatureOperation if want_cut
                                 else adsk.fusion.FeatureOperations.JoinFeatureOperation)
            combine.isKeepToolBodies = False
            result = root.features.combineFeatures.add(combine)
            result.name = name + (" cut" if want_cut else " raise")
            made.append(result)
    except Exception as exc:
        # Don't leave the tools lying about as bodies of their own.
        for thing in reversed(made):
            if thing.isValid:
                thing.deleteMe()
        if not parametric:
            for _, body in added:
                if body.isValid:
                    body.deleteMe()
        raise RuntimeError("Fusion couldn't combine the text with %s: %s" % (target.name, exc))
    made.reverse()
    return made


def add_body(design, piece, name):
    """Add a temporary body to the design as a new body. Returns the features
    made (or the body, in a direct-modelling design) and the body."""
    root = design.rootComponent
    if design.designType != adsk.fusion.DesignTypes.ParametricDesignType:
        body = root.bRepBodies.add(piece)
        body.name = name
        return [body], body
    feature = root.features.baseFeatures.add()
    feature.startEdit()
    root.bRepBodies.add(piece, feature)
    feature.finishEdit()
    feature.name = name
    body = feature.bodies.item(0)
    body.name = name
    return [feature], body


def fillet(design, body, frame, places, convex_radius, concave_radius):
    """Round the sharp edges where the walls meet the surface, the floors and
    the tops: outside corners (the top of a cut, the top of a raise) by
    convex_radius, inside ones (a cut's floor, a raise's foot) by
    concave_radius, in cm; 0 leaves them sharp. Returns the fillet feature,
    or None if there was nothing to round."""
    convex, concave = fillet_edges(body, frame, places)
    return add_fillet(design, convex, concave, convex_radius, concave_radius)


def fillet_edges(body, frame, places):
    """The edges fillet rounds: (outside corners, inside corners), as lists.

    An edge qualifies when one of its faces is a cylinder about the same
    axis, the other is one of the walls (any other face: an Emboss makes
    straight walls flat), and it lies within one of the text patches
    (`places`), so the body's own edges are left alone."""
    cylinder_type = adsk.core.SurfaceTypes.CylinderSurfaceType
    axis = frame["axis"]

    def coaxial(f):
        g = f.geometry
        return g.surfaceType == cylinder_type and abs(abs(_dot(_unit(_tuple(g.axis)), axis)) - 1) < 1e-6

    def in_a_patch(p):
        for place in places:
            d = _sub(p, place["centre"])
            along = _dot(d, place["up"])
            sideways = math.atan2(_dot(d, place["right"]), _dot(d, place["x"]))
            if abs(along) <= place["length"] / 2 and abs(sideways) <= place["angle"] / 2:
                return True
        return False

    sets = {True: [], False: []}
    for e in body.edges:
        if e.faces.count != 2:
            continue
        a, b = e.faces.item(0), e.faces.item(1)
        if coaxial(a) and not coaxial(b):
            cyl, wall = a, b
        elif coaxial(b) and not coaxial(a):
            cyl, wall = b, a
        else:
            continue
        _, s0, s1 = e.evaluator.getParameterExtents()
        middle = (s0 + s1) / 2
        _, point = e.evaluator.getPointAtParameter(middle)
        if not in_a_patch(_tuple(point)):
            continue
        # Convex when the two outward normals turn the same way as the
        # cylinder face's loop runs along the edge.
        _, tangent = e.evaluator.getFirstDerivative(middle)
        tangent = _tuple(tangent)
        for coedge in e.coEdges:
            if coedge.loop.face == cyl and coedge.isOpposedToEdge:
                tangent = _scale(tangent, -1)
        _, n_cyl = cyl.evaluator.getNormalAtPoint(point)
        _, n_wall = wall.evaluator.getNormalAtPoint(point)
        convex = _dot(_cross(_tuple(n_cyl), _tuple(n_wall)), tangent) > 0
        sets[convex].append(e)
    return sets[True], sets[False]


def add_fillet(design, convex, concave, convex_radius, concave_radius):
    """One fillet feature: the convex edges by convex_radius, the concave by
    concave_radius (cm; 0 leaves them). None if there is nothing to round.
    Every qualifying edge is passed in, so tangent chaining is left off: it
    adds nothing, and took three times as long."""
    fillet_input = design.rootComponent.features.filletFeatures.createInput()
    any_edges = False
    for edges, size in ((convex, convex_radius), (concave, concave_radius)):
        if size > 0 and edges:
            collection = adsk.core.ObjectCollection.create()
            for e in edges:
                collection.add(e)
            fillet_input.edgeSetInputs.addConstantRadiusEdgeSet(
                collection, adsk.core.ValueInput.createByReal(size), False)
            any_edges = True
    if not any_edges:
        return None
    return design.rootComponent.features.filletFeatures.add(fillet_input)


def moved(body, frame, angle_deg, offset):
    """A temporary body turned round the cylinder's axis and slid along it,
    the way placement moves a patch for its angle and offset."""
    temp = adsk.fusion.TemporaryBRepManager.get()
    copy = temp.copy(body)
    matrix = adsk.core.Matrix3D.create()
    turn = adsk.core.Matrix3D.create()
    turn.setToRotation(frame["sign"] * math.radians(angle_deg), _vector(frame["axis"]), _point(frame["mid"]))
    matrix.transformBy(turn)
    slide = adsk.core.Matrix3D.create()
    slide.translation = _vector(_scale(frame["axis"], offset))
    matrix.transformBy(slide)
    temp.transform(copy, matrix)
    return copy


def pocketed_copy(body, frame, pockets):
    """A temporary copy of `body` with a shallow pocket under each patch.

    While previewing a smooth surface, this is drawn in place of the body,
    which would otherwise hide everything that sinks into it. pockets is a
    list of (place, inset, deep): each pocket is `inset` smaller all round
    than its patch, where the patch still lies on the cylinder, so the patch
    covers it completely; and `deep` deep, past the deepest point. Returns
    None if Fusion can't cut it.
    """
    temp = adsk.fusion.TemporaryBRepManager.get()
    copy = temp.copy(body)
    for place, inset, deep in pockets:
        sector = _sector(frame, place, inset, deep)
        if sector is False:
            return None
        if sector is not None and not temp.booleanOperation(
                copy, sector, adsk.fusion.BooleanTypes.DifferenceBooleanType):
            return None
    return copy


def _sector(frame, place, inset, deep):
    """The pocket under one patch (see pocketed_copy): None if none is needed,
    False if Fusion can't make it."""
    if deep <= 0:
        return None                      # nothing sinks in
    # A little past the surface, so the cut opens it.
    return sector(frame, place, -deep, 0.01, inset)


def sector(frame, place, low, high, inset=0.0):
    """A temporary solid over a patch, between offsets `low` and `high` (cm
    out from the surface, toward the air), `inset` in from the patch's edges.
    None if the inset leaves nothing; False if Fusion can't make it."""
    temp = adsk.fusion.TemporaryBRepManager.get()
    radius, sign = frame["radius"], frame["sign"]
    half_angle = place["angle"] / 2 - inset / radius
    half_length = place["length"] / 2 - inset
    if half_angle <= 0 or half_length <= 0:
        return None
    r1, r2 = radius + sign * low, radius + sign * high
    inner, outer = min(r1, r2), max(r1, r2)
    centre, up = place["centre"], place["up"]
    x, right = place["x"], place["right"]
    ring = temp.createCylinderOrCone(_point(_add(centre, _scale(up, -half_length))), outer,
                                     _point(_add(centre, _scale(up, half_length))), outer)
    if inner > 1e-6:
        core = temp.createCylinderOrCone(_point(_add(centre, _scale(up, -half_length - 1))), inner,
                                         _point(_add(centre, _scale(up, half_length + 1))), inner)
        if not temp.booleanOperation(ring, core, adsk.fusion.BooleanTypes.DifferenceBooleanType):
            return False

    # Cut the ring down to the patch's angle, as wedges of at most 120°: each
    # is what lies on the far side of one plane through the axis and the near
    # side of another, so each must be under 180°.
    big = 4 * (outer + half_length) + 1
    pieces = max(1, math.ceil(2 * half_angle / (2 * math.pi / 3)))
    step = 2 * half_angle / pieces
    whole = None
    for k in range(pieces):
        wedge = temp.copy(ring)
        for a, side in ((-half_angle + k * step, 1), (-half_angle + (k + 1) * step, -1)):
            along = _add(_scale(x, math.cos(a)), _scale(right, math.sin(a)))
            normal = _add(_scale(x, -math.sin(a)), _scale(right, math.cos(a)))
            half_space = temp.createBox(adsk.core.OrientedBoundingBox3D.create(
                _point(_add(centre, _scale(normal, side * big / 2))),
                _vector(along), _vector(normal), 2 * big, big, 2 * big))
            if not temp.booleanOperation(wedge, half_space,
                                         adsk.fusion.BooleanTypes.IntersectionBooleanType):
                return False
        if whole is None:
            whole = wedge
        elif not temp.booleanOperation(whole, wedge, adsk.fusion.BooleanTypes.UnionBooleanType):
            return False
    return whole


def _sectors(frame, areas, low, high):
    """One temporary solid over several patches: areas is (place, inset) pairs."""
    temp = adsk.fusion.TemporaryBRepManager.get()
    whole = None
    for place, inset in areas:
        piece = sector(frame, place, low, high, inset)
        if piece is False:
            raise RuntimeError("Fusion could not make the solid over the text.")
        if piece is None:
            continue
        if whole is None:
            whole = piece
        elif not temp.booleanOperation(whole, piece, adsk.fusion.BooleanTypes.UnionBooleanType):
            raise RuntimeError("Fusion could not join the solids over the text.")
    return whole


def _without_scaffold(piece, frame, low):
    """Delete the faces a sector added (its flat sides and its bottom at
    `low`), leaving the text area's own faces as an open temporary body."""
    temp = adsk.fusion.TemporaryBRepManager.get()
    radius, sign = frame["radius"], frame["sign"]
    floor = radius + sign * low
    plane = adsk.core.SurfaceTypes.PlaneSurfaceType
    cylinder = adsk.core.SurfaceTypes.CylinderSurfaceType
    scaffold = [f for f in piece.faces
                if f.geometry.surfaceType == plane
                or (f.geometry.surfaceType == cylinder and abs(f.geometry.radius - floor) < 1e-6)]
    if scaffold and not temp.deleteFaces(scaffold, True):
        raise RuntimeError("Fusion could not trim the text area.")
    return piece


def text_area(result, frame, areas, low, high):
    """Just the text areas of a temporary solid, as an open body: what the
    preview shows when the whole body isn't wanted."""
    temp = adsk.fusion.TemporaryBRepManager.get()
    region = _sectors(frame, areas, low, high)
    piece = temp.copy(result)
    if region is None or not temp.booleanOperation(
            piece, region, adsk.fusion.BooleanTypes.IntersectionBooleanType):
        raise RuntimeError("Fusion could not pick out the text area.")
    return _without_scaffold(piece, frame, low)


def sharp_surface(surface, frame, areas, tools, low, high):
    """Sharp-edged text in a surface body, which has no material to cut: the
    same tools applied to a thin solid under each text area, its own sides
    and bottom then removed, and the matching patch cut out of the surface.
    Returns (the surface with holes, the text areas' faces), two temporary
    open bodies that meet edge to edge, to show or to stitch.

    areas      (place, inset) for each block
    low, high  offsets (cm) beyond the deepest floor and the highest top"""
    temp = adsk.fusion.TemporaryBRepManager.get()
    core = _sectors(frame, areas, low, 0.0)
    if core is None:
        raise RuntimeError("The text area is too small.")
    for cut in (False, True):
        for is_cut, solid in tools:
            if is_cut == cut and not temp.booleanOperation(
                    core, solid, adsk.fusion.BooleanTypes.DifferenceBooleanType if cut
                    else adsk.fusion.BooleanTypes.UnionBooleanType):
                raise RuntimeError("Fusion could not apply a layer to the surface.")
    shell = _without_scaffold(core, frame, low)
    holed = temp.copy(surface)
    if not temp.booleanOperation(holed, _sectors(frame, areas, low, high),
                                 adsk.fusion.BooleanTypes.DifferenceBooleanType):
        raise RuntimeError("Fusion could not open the surface for the text.")
    return holed, shell


def add_stitched(design, pieces, name):
    """Add temporary open bodies to the design and stitch them into one
    surface body. Returns the features made, last first, and the body."""
    root = design.rootComponent
    parametric = design.designType == adsk.fusion.DesignTypes.ParametricDesignType
    made = []
    feature = None
    if parametric:
        feature = root.features.baseFeatures.add()
        feature.startEdit()
    for piece in pieces:
        if parametric:
            root.bRepBodies.add(piece, feature)
        else:
            root.bRepBodies.add(piece)
    if parametric:
        feature.finishEdit()
        feature.name = name + " pieces"
        made.append(feature)
    added = adsk.core.ObjectCollection.create()
    for k in range(len(pieces)):
        added.add(root.bRepBodies.item(root.bRepBodies.count - len(pieces) + k))
    stitch_input = root.features.stitchFeatures.createInput(
        added, adsk.core.ValueInput.createByReal(0.0005),
        adsk.fusion.FeatureOperations.NewBodyFeatureOperation)
    stitch = root.features.stitchFeatures.add(stitch_input)
    stitch.name = name
    made.append(stitch)
    result = stitch.bodies.item(0)
    result.name = name
    made.reverse()
    return made, result


# How finely show_body meshes, and how closely its edge lines follow the edges (cm).
QUALITY = adsk.fusion.TriangleMeshQualityOptions.HighQualityTriangleMesh
EDGE_TOLERANCE = 0.001


def show_body(group, body, color, edges=True):
    """Draw a temporary body in `group` as a triangle mesh, with its edges as
    black lines the way Fusion draws a body.

    Fusion can draw a body directly, but for one with hundreds of small faces
    the viewport took 27 s to redraw it; meshing it here takes under half a
    second and draws at once. High quality, so walls and fillets shade
    smoothly. (Setting a tolerance and a normal-angle limit instead made
    Fusion mesh one body without end.)"""
    calculator = body.meshManager.createMeshCalculator()
    calculator.setQuality(QUALITY)
    mesh = calculator.calculate()
    drawn = group.addMesh(adsk.fusion.CustomGraphicsCoordinates.create(mesh.nodeCoordinatesAsDouble),
                          mesh.nodeIndices, mesh.normalVectorsAsDouble, [])
    drawn.color = color
    drawn.isSelectable = False
    if edges:
        coordinates, lengths = [], []
        for edge in body.edges:
            evaluator = edge.evaluator
            ok, start, end = evaluator.getParameterExtents()
            ok, points = evaluator.getStrokes(start, end, EDGE_TOLERANCE) if ok else (False, [])
            if ok and len(points) > 1:
                for p in points:
                    coordinates += [p.x, p.y, p.z]
                lengths.append(len(points))
        if lengths:
            lines = group.addLines(adsk.fusion.CustomGraphicsCoordinates.create(coordinates), [], True, lengths)
            lines.color = adsk.fusion.CustomGraphicsSolidColorEffect.create(adsk.core.Color.create(0, 0, 0, 255))
            lines.weight = 1
            lines.depthPriority = 1      # over the faces they bound
            lines.isSelectable = False
    return drawn


def preview_mesh(group, points, nu, nv, color):
    """Show the surface as custom graphics in `group`, without making a body.

    Uses the points the surface passes through at its knots (each control
    point averaged 1-4-1 with its neighbours both ways), which is what the
    real surface will look like to well within a grid spacing.
    """
    def smooth(values, n, stride):
        out = list(values)
        for k in range(len(values)):
            idx = (k // stride) % n
            if 0 < idx < n - 1:
                p, q, r = values[k - stride], values[k], values[k + stride]
                out[k] = ((p[0] + 4 * q[0] + r[0]) / 6,
                          (p[1] + 4 * q[1] + r[1]) / 6,
                          (p[2] + 4 * q[2] + r[2]) / 6)
        return out

    pts = smooth(smooth(points, nu, nv), nv, 1)
    coords, normals = [], []
    for i in range(nu):
        i0, i1 = max(i - 1, 0), min(i + 1, nu - 1)
        for j in range(nv):
            j0, j1 = max(j - 1, 0), min(j + 1, nv - 1)
            du = _sub(pts[i1 * nv + j], pts[i0 * nv + j])
            dv = _sub(pts[i * nv + j1], pts[i * nv + j0])
            n = _cross(du, dv)       # points the same way as the face
            length = math.sqrt(_dot(n, n)) or 1.0
            coords.extend(pts[i * nv + j])
            normals.extend((n[0] / length, n[1] / length, n[2] / length))
    triangles = []
    for i in range(nu - 1):
        for j in range(nv - 1):
            k = i * nv + j
            triangles.extend((k, k + nv, k + nv + 1, k, k + nv + 1, k + 1))
    mesh = group.addMesh(adsk.fusion.CustomGraphicsCoordinates.create(coords),
                         triangles, normals, [])
    mesh.color = color
    mesh.isSelectable = False
    return mesh


def _cuts(count, tile_spans, busy=None):
    """Knot values to cut a clamped uniform spline of `count` control points
    at, so no piece has more than tile_spans spans.

    With `busy` (a flag per control point, True where the surface shows in
    the result) the seams between faces are kept out of the finished walls
    where they can be: a stretch where the surface shows gets a tile of its
    own up to twice the span, and is only cut through when it is longer.
    Elsewhere the tiles stay small, since that's quickest."""
    spans = count - DEGREE
    if not tile_spans or spans <= tile_spans:
        return []
    if busy is None:
        pieces = math.ceil(spans / tile_spans)
        return [float(round(s * spans / pieces)) for s in range(1, pieces)]
    # The surface at knot k depends on control points k to k + 3.
    free = [not any(busy[k:k + DEGREE + 1]) for k in range(spans + 1)]
    cuts, at = [], 0
    while spans - at > tile_spans:
        target = at + tile_spans
        if not free[target]:
            start = target
            while start > at and not free[start]:
                start -= 1
            end = target
            while end < spans and not free[end]:
                end += 1
            if end - at <= 2 * tile_spans and end < spans:
                target = end                 # take the whole stretch in this tile
            elif start - at >= tile_spans // 4:
                target = start               # stop short of it; the next tile takes it
        cuts.append(float(target))
        at = target
    return cuts


def _split(seq, knots, cuts):
    """Cut a B-spline at the given knot values. Returns [(points, knots)].

    seq holds the control points along the direction being cut; each one may
    be a whole column of (x, y, z) tuples, which cuts a surface.
    """
    seq, knots = list(seq), list(knots)
    for c in cuts:
        while knots.count(c) < DEGREE:
            seq, knots = _insert_knot(seq, knots, c)
    pieces, first, lead = [], 1, knots[0]
    for c in cuts:
        m = knots.index(c)
        # The spline passes through control point m-1 here; it ends one
        # piece and starts the next, each clamped by one more copy of c.
        pieces.append((seq[first - 1:m], [lead] + knots[first:m + DEGREE] + [c]))
        first, lead = m, c
    pieces.append((seq[first - 1:], [lead] + knots[first:]))
    return pieces


def _insert_knot(seq, knots, u):
    """Boehm's knot insertion: the same spline with one more knot at u."""
    k = bisect.bisect_right(knots, u) - 1
    new = seq[:k - DEGREE + 1]
    for i in range(k - DEGREE + 1, k + 1):
        t = (u - knots[i]) / (knots[i + DEGREE] - knots[i])
        new.append([(p[0] + (q[0] - p[0]) * t, p[1] + (q[1] - p[1]) * t,
                     p[2] + (q[2] - p[2]) * t) for p, q in zip(seq[i - 1], seq[i])])
    new += seq[k:]
    return new, knots[:k + 1] + [u] + knots[k + 1:]


def _transpose(grid):
    return [list(row) for row in zip(*grid)]


def add_to_design(design, body, name):
    """Put a transient body into the root component. Returns what to delete
    to take it out again: the base feature, or the body in a direct design."""
    root = design.rootComponent
    if design.designType == adsk.fusion.DesignTypes.ParametricDesignType:
        feature = root.features.baseFeatures.add()
        feature.startEdit()
        root.bRepBodies.add(body, feature).name = name
        feature.finishEdit()
        feature.name = name
        return feature
    added = root.bRepBodies.add(body)
    added.name = name
    return added


def _clamped_knots(count, degree=DEGREE):
    inner = [float(k) for k in range(1, count - degree)]
    end = float(count - degree)
    return [0.0] * (degree + 1) + inner + [end] * (degree + 1)


def _point(p):
    return adsk.core.Point3D.create(p[0], p[1], p[2])


def _vector(v):
    return adsk.core.Vector3D.create(v[0], v[1], v[2])


def _newell(polygon):
    """A polygon's normal, its length twice the area; follows the winding."""
    n = [0.0, 0.0, 0.0]
    for k, a in enumerate(polygon):
        b = polygon[(k + 1) % len(polygon)]
        n[0] += (a[1] - b[1]) * (a[2] + b[2])
        n[1] += (a[2] - b[2]) * (a[0] + b[0])
        n[2] += (a[0] - b[0]) * (a[1] + b[1])
    return tuple(n)


def _tuple(v):
    return (v.x, v.y, v.z)


def _add(a, b):
    return (a[0] + b[0], a[1] + b[1], a[2] + b[2])


def _sub(a, b):
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def _scale(a, k):
    return (a[0] * k, a[1] * k, a[2] * k)


def _dot(a, b):
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def _cross(a, b):
    return (a[1] * b[2] - a[2] * b[1],
            a[2] * b[0] - a[0] * b[2],
            a[0] * b[1] - a[1] * b[0])


def _unit(a):
    return _scale(a, 1 / math.sqrt(_dot(a, a)))


def _off_axis(v, axis):
    """The part of v perpendicular to a unit axis."""
    return _sub(v, _scale(axis, _dot(v, axis)))
