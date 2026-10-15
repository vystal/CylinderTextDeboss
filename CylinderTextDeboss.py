"""Cylinder Text Deboss.

A palette for sinking text into (or raising it from) a cylinder. The palette
turns the text into height maps through the chosen style and profile (see
palette/index.html); this file picks the cylinder and hands them to wrap.py,
which builds a smooth surface or cuts sharp-edged layers into the body, and
shows either as a live preview first.
"""

import array
import base64
import json
import math
import os
import time
import traceback

import adsk.core
import adsk.fusion

from . import wrap

CMD_ID = "CylinderTextDeboss_Show"
PALETTE_ID = "CylinderTextDeboss_Palette"
WORKSPACE_ID = "FusionSolidEnvironment"
PANEL_IDS = ("SolidCreatePanel", "SurfaceCreatePanel")
HERE = os.path.dirname(os.path.abspath(__file__))
PALETTE_URL = os.path.join(HERE, "palette", "index.html").replace("\\", "/")

# Spans per face when the surface is built in tiles. Measured fastest of 15,
# 25, 40 and 60: 24,000 control points in 0.34 s as 48 faces, where one face
# took about 11 s and left Fusion not responding.
TILE_SPANS = 25
# Spans per face for the surfaces that make sharp-edged cuts. Bigger, on a
# grid half as fine, and placed so the joins fall outside the finished walls
# where they can: measured on "Ag", 7 wall faces in 1.7 s, where the smooth
# surface's tiling left 60.
SHARP_TILE_SPANS = 45
# When Fusion can't fit the fillets asked for (in the narrowest gaps between
# walls), they are tried again this much smaller each time.
FILLET_RETRIES = (1.0, 0.7, 0.5, 0.35, 0.25)

app = adsk.core.Application.get()
ui = app.userInterface

_handlers = []      # Fusion drops handlers nobody holds a reference to.
_target = None      # (face, eye, token) from the last pick; eye fixes where
                    # angle 0 is, and the token finds the face again after the
                    # body changes
_last = None        # what the last Generate made, for "replace previous"
_maps = {}          # block key -> the palette's last height map for it, kept
                    # so moving the text doesn't mean sending it again
_tool_cache = {}    # block at rest -> its sharp-edged tools, so moving a block
                    # only turns and slides them
_preview = None     # custom graphics group showing the live preview
_hidden = []        # (body, was it showing) for bodies hidden while previewing
_before = None      # a copy of the body as it was before the last sharp-edged
                    # Generate, to preview a replacement on
_restore = []       # (body, was it showing) for surface bodies a sharp-edged
                    # Generate hid, to show again if it is replaced
_context = None     # (key, pocketed copy of the body) for the preview, kept
                    # until the text's patch moves or grows


def run(context):
    try:
        command = ui.commandDefinitions.itemById(CMD_ID) or \
            ui.commandDefinitions.addButtonDefinition(
                CMD_ID, "Cylinder Text",
                "Put text on a cylindrical face: sunk, raised, outlined or in a panel; "
                "as a smooth surface or cut into the body with sharp edges.",
                os.path.join(HERE, "resources", ""))
        _hold(command.commandCreated, _CommandCreated())
        workspace = ui.workspaces.itemById(WORKSPACE_ID)
        for panel_id in PANEL_IDS:
            panel = workspace.toolbarPanels.itemById(panel_id)
            if not panel.controls.itemById(CMD_ID):
                panel.controls.addCommand(command)
    except Exception:
        ui.messageBox("Cylinder Text failed to start:\n" + traceback.format_exc())


def stop(context):
    try:
        _clear_preview()
        workspace = ui.workspaces.itemById(WORKSPACE_ID)
        for panel_id in PANEL_IDS:
            control = workspace.toolbarPanels.itemById(panel_id).controls.itemById(CMD_ID)
            if control:
                control.deleteMe()
        for thing in (ui.commandDefinitions.itemById(CMD_ID),
                      ui.palettes.itemById(PALETTE_ID)):
            if thing:
                thing.deleteMe()
    except Exception:
        ui.messageBox("Cylinder Text failed to stop:\n" + traceback.format_exc())


def _hold(event, handler):
    event.add(handler)
    _handlers.append(handler)


def _show_palette():
    palette = ui.palettes.itemById(PALETTE_ID)
    if palette is None:
        palette = ui.palettes.add(PALETTE_ID, "Cylinder Text", PALETTE_URL,
                                  True, True, True, 340, 760)
        palette.dockingState = adsk.core.PaletteDockingStates.PaletteDockStateRight
        _hold(palette.incomingFromHTML, _Incoming())
        _hold(palette.closed, _PaletteClosed())
    palette.isVisible = True


def _drop_graphics():
    global _preview
    if _preview is not None and _preview.isValid:
        _preview.deleteMe()
    _preview = None


def _clear_preview():
    """Take the preview away and show the bodies it hid again."""
    global _hidden, _context
    _drop_graphics()
    for body, showing in _hidden:
        if body.isValid:
            body.isLightBulbOn = showing
    _hidden = []
    _context = None
    app.activeViewport.refresh()


def _look(body):
    """How the preview is coloured: like the body, in its own appearance, so
    the preview shows what the result will look like."""
    try:
        if body.appearance is not None:
            return adsk.fusion.CustomGraphicsAppearanceColorEffect.create(body.appearance)
    except Exception:
        pass
    grey = adsk.core.Color.create
    return adsk.fusion.CustomGraphicsBasicMaterialColorEffect.create(
        grey(165, 165, 170, 255), grey(45, 45, 50, 255), grey(90, 90, 90, 255),
        grey(0, 0, 0, 255), 40.0, 1.0)


# ---------------------------------------------------------------------------
# What the palette can ask for. Each takes the parsed JSON it sent and returns
# a dict that goes back as JSON; {"ok": False, "message": ...} for a refusal.
# ---------------------------------------------------------------------------

def _pick(_data):
    """Use what is selected if it will do, otherwise ask for a selection."""
    global _target
    selected = ui.activeSelections
    face = wrap.cylinder_face(selected.item(0).entity) if selected.count else None
    if face is None:
        try:
            chosen = ui.selectEntity("Select a cylindrical face or a body",
                                     "CylindricalFaces,SolidBodies,SurfaceBodies")
        except Exception:  # selectEntity raises when the selection is cancelled
            return {"ok": False, "message": "Selection cancelled."}
        face = wrap.cylinder_face(chosen.entity)
        if face is None:
            return {"ok": False, "message": "That has no cylindrical face."}
    global _before
    eye = app.activeViewport.camera.eye
    _clear_preview()     # it belongs to the old target
    _target = (face, eye, face.entityToken)
    _before = None
    frame = wrap.cylinder_frame(face, eye)
    return {"ok": True, "radiusMm": frame["radius"] * 10,
            "lengthMm": frame["length"] * 10,
            "bore": frame["sign"] < 0, "body": face.body.name}


class _Refused(Exception):
    """A request that can't go ahead; the reply carries these fields."""


def _decode(text):
    """A field from the palette: base64 of little-endian float32s."""
    values = array.array("f")
    values.frombytes(base64.b64decode(text))
    return values.tolist()


def _blocks(data):
    """The blocks a request is about, each (block, height map), with the maps
    the palette sent this time stored and the rest taken from the store.

    The height map only comes when it has changed. They are kept before
    anything else is checked, so the palette never needs to send one twice;
    if one is missing (the add-in was restarted, say), it is asked for."""
    keys = {b["key"] for b in data["blocks"]}
    for key in list(_maps):
        if key not in keys:
            del _maps[key]
    for b in data["blocks"]:
        if b.get("map"):
            m = dict(b["map"], id=b["mapId"])
            if "relief" in m:
                m["relief"] = _decode(m["relief"])
            m["layers"] = [dict(layer, wall=_decode(layer["wall"]),
                                cap=_decode(layer["cap"]) if layer.get("cap") else None)
                           for layer in m.get("layers", [])]
            _maps[b["key"]] = m
    found = [(b, _maps.get(b["key"])) for b in data["blocks"]]
    if any(m is None or m["id"] != b["mapId"] for b, m in found):
        raise _Refused({"needMap": True, "message": "Height map needed."})
    return found


def _setting(data):
    """The design, the picked face and its frame, checked."""
    global _target
    if _target is None:
        raise _Refused({"message": "Pick a cylinder first."})
    design = adsk.fusion.Design.cast(app.activeProduct)
    if design is None:
        raise _Refused({"message": "Switch to the Design workspace first."})
    face, eye, token = _target
    if not face.isValid:
        # Features added or deleted since the pick can leave the old reference
        # stale; the token finds the same face again.
        again = [f for f in design.findEntityByToken(token) if adsk.fusion.BRepFace.cast(f)]
        if not again:
            raise _Refused({"message": "The picked face has gone; pick the cylinder again."})
        face = adsk.fusion.BRepFace.cast(again[0])
        _target = (face, eye, token)
    return design, face, wrap.cylinder_frame(face, eye)


def _where(b, m, at_rest=False):
    """Placement arguments for a block, in cm. at_rest leaves out its angle
    and offset, which only turn and slide it."""
    return (m["widthMm"] / 10, m["heightMm"] / 10,
            0.0 if at_rest else b["angleDeg"], 0.0 if at_rest else b["offsetMm"] / 10,
            b["flip"], b["direction"] == "lengthways")


def _net(frame, m, where, offsets, pinned):
    """Control points from a field of offsets in mm toward the air."""
    return wrap.control_points(frame, [-v for v in offsets], m["nu"], m["nv"],
                               where[0], where[1], 0.1, *where[2:], pinned=pinned)


def _busy(layer, nu, nv):
    """Where a layer's wall passes through the depth it works at, by control
    column and row: the only places seams between faces would show."""
    wall, cut = layer["wall"], layer["cut"]
    edge = layer["high"] + 0.1 if cut else layer["low"] - 0.1
    columns, rows = [False] * nu, [False] * nv
    for r in range(nv):
        j = nv - 1 - r
        row = wall[r * nu:(r + 1) * nu]
        for i, v in enumerate(row):
            if (v < edge) if cut else (v > edge):
                columns[i] = True
                rows[j] = True
    return columns, rows


def _tools(frame, face, found):
    """The exact solids every block's layers cut out or add, (cut, solid),
    and each block's placement.

    They're built with the block at rest and then turned and slid into
    place, so moving a block reuses what was built for it."""
    tools, places = [], []
    for b, m in found:
        rest = _where(b, m, at_rest=True)
        key = (_target[2], b["key"], m["id"], rest)
        if key not in _tool_cache:
            place = wrap.placement(frame, *rest)
            built = []
            for layer in m["layers"]:
                wall = _net(frame, m, rest, layer["wall"], False)
                cap = _net(frame, m, rest, layer["cap"], False) if layer["cap"] else None
                furthest = max(layer["wall"]) if layer["cut"] else min(layer["wall"])
                solid = wrap.layer_tool(frame, place, wall, m["nu"], m["nv"], layer["cut"],
                                        layer["low"] / 10, layer["high"] / 10, cap,
                                        SHARP_TILE_SPANS, _busy(layer, m["nu"], m["nv"]),
                                        clear=furthest / 10)
                built.append((layer["cut"], solid))
            _tool_cache[key] = built
        for cut, solid in _tool_cache[key]:
            tools.append((cut, wrap.moved(solid, frame, b["angleDeg"], b["offsetMm"] / 10)))
        places.append(wrap.placement(frame, *_where(b, m)))
    # Only what this request used is worth keeping.
    used = {(_target[2], b["key"], m["id"], _where(b, m, at_rest=True)) for b, m in found}
    for key in list(_tool_cache):
        if key not in used:
            del _tool_cache[key]
    return tools, places


def _hide_body(body):
    if not any(b == body for b, _ in _hidden):
        _hidden.append((body, body.isLightBulbOn))
        body.isLightBulbOn = False


def _hidden_by_you(body):
    """Whether the body was hidden by the person, rather than by the preview.
    The preview then shows only the text areas: drawing the whole body would
    put back something they had hidden, and the browser can't hide that."""
    for b, showing in _restore + _hidden:
        if b == body:
            return not showing
    return not body.isLightBulbOn


def _replacing(data):
    """Whether this request will replace what the last Generate made."""
    return bool(data.get("replace")) and any(t is not None and t.isValid for t in (_last or []))


def _areas(frame, found):
    """Each block's patch, and how far in from its edges its text area stops
    (two grid spacings, where nothing happens)."""
    return [(wrap.placement(frame, *_where(b, m)), 2 * m["widthMm"] / 10 / (m["nu"] - 1))
            for b, m in found]


def _reach(found):
    """Offsets (cm) a little beyond every layer's deepest floor and highest top."""
    lows = [layer["low"] for _, m in found for layer in m.get("layers", [])] + [0.0]
    highs = [layer["high"] for _, m in found for layer in m.get("layers", [])] + [0.0]
    return min(lows) / 10 - 0.05, max(highs) / 10 + 0.05


def _linked(body):
    """The name of the linked component the body is in, or None. A linked
    (inserted) component's bodies can't be changed from this design."""
    occurrence = body.assemblyContext
    while occurrence is not None:
        if occurrence.isReferencedComponent:
            return occurrence.component.name
        occurrence = occurrence.assemblyContext
    return None


def _applied(body, tools):
    """A temporary copy of the body with the tools applied: raises, then cuts."""
    temp = adsk.fusion.TemporaryBRepManager.get()
    result = temp.copy(body)
    for raise_first in (True, False):
        for cut, solid in tools:
            if cut != raise_first:
                temp.booleanOperation(result, solid, adsk.fusion.BooleanTypes.DifferenceBooleanType
                                      if cut else adsk.fusion.BooleanTypes.UnionBooleanType)
    return result


def _show_preview(data):
    """The result as it will look, in the body's own appearance. The real
    body would hide anything sunk into it, so it is hidden while this shows
    and a temporary result is drawn in its place: for a smooth surface, a
    copy of the body with a shallow pocket under each patch and the surface
    over it; for sharp edges, the exact result of the cut. With "whole body"
    off, or the body hidden by the person, only the text areas are drawn."""
    global _preview, _context
    found = _blocks(data)
    design, face, frame = _setting(data)
    body = face.body
    look = _look(body)
    temp = adsk.fusion.TemporaryBRepManager.get()
    hidden = _hidden_by_you(body)
    whole = data.get("wholeBody", True) and not hidden

    if _replacing(data):
        # Show what will replace the last result, not both.
        for thing in _last:
            if thing.isValid:
                for made in (thing.bodies if hasattr(thing, "bodies") else [thing]):
                    _hide_body(made)

    if data["mode"] == "sharp":
        tools, _ = _tools(frame, face, found)
        areas = _areas(frame, found)
        low, high = _reach(found)
        if body.isSolid:
            start = _before if (_replacing(data) and _before is not None) else body
            result = _applied(start, tools)
            shown = [result] if whole else [wrap.text_area(result, frame, areas, low, high)]
        else:
            holed, shell = wrap.sharp_surface(body, frame, areas, tools, low, high)
            shown = [holed, shell] if whole else [shell]
        _drop_graphics()
        _preview = design.rootComponent.customGraphicsGroups.add()
        for piece in shown:
            wrap.show_body(_preview, piece, look)
        _hide_body(body)
        app.activeViewport.refresh()
        notes = []
        if hidden:
            notes.append("Showing just the text area, as the body is hidden.")
        if data["rimFilletMm"] > 0 or data["floorFilletMm"] > 0:
            notes.append("Fillets are added on Generate.")
        return {"ok": True, "note": " ".join(notes)}

    nets, pockets = [], []
    for b, m in found:
        where = _where(b, m)
        nets.append((_net(frame, m, where, m["relief"], True), m))
        place = wrap.placement(frame, *where)
        # Deep enough for the deepest point, rounded up so small changes keep the copy.
        deep = math.ceil(max(-min(m["relief"]), 0) / 10 * 1.2 * 200) / 200
        spacing = m["widthMm"] / 10 / (m["nu"] - 1)
        pockets.append((place, 2 * spacing, deep))
    _drop_graphics()
    _preview = design.rootComponent.customGraphicsGroups.add()
    if whole:
        key = (body.entityToken, tuple((round(pl["angle"], 9), round(pl["length"], 9),
                                        tuple(round(c, 9) for c in pl["centre"] + pl["x"]), deep, inset)
                                       for pl, inset, deep in pockets))
        if _context is None or _context[0] != key:
            _context = (key, wrap.pocketed_copy(body, frame, pockets))
        if _context[1] is not None:
            wrap.show_body(_preview, _context[1], look, edges=False)
    _hide_body(body)
    for points, m in nets:
        wrap.preview_mesh(_preview, points, m["nu"], m["nv"], look)
    app.activeViewport.refresh()
    return {"ok": True, "note": "Showing just the text surface, as the body is hidden." if hidden else ""}


def _hide_preview(_data):
    _clear_preview()
    return {"ok": True}


def _fillets(design, made, frame, places, data, context=None):
    """Round the new edges if asked, smaller each time Fusion can't fit them.
    context is the occurrence the body is in, if it isn't the root component:
    a feature gives back the body in its own component's coordinates, and the
    edges are found in the design's. Returns a warning, or ""."""
    rim, floor = data["rimFilletMm"] / 10, data["floorFilletMm"] / 10
    if rim <= 0 and floor <= 0:
        return ""
    for scale in FILLET_RETRIES:
        # The body as the last feature left it.
        target = made[0].bodies.item(0) if hasattr(made[0], "bodies") else made[0]
        if context is not None and target.assemblyContext is None:
            target = target.createForAssemblyContext(context)
        try:
            rounded = wrap.fillet(design, target, frame, places, rim * scale, floor * scale)
        except Exception:
            continue
        if rounded is None:
            return "No edges were found to fillet, so the edges were left sharp."
        made.insert(0, rounded)
        if scale < 1:
            return ("Fusion couldn't fit the fillets at that size, so they were made %.0f%% "
                    "as big: %.3f mm on top, %.3f mm at the floor."
                    % (scale * 100, rim * scale * 10, floor * scale * 10))
        return ""
    return "Fusion couldn't fit the fillets even much smaller, so the edges were left sharp."


def _generate(data):
    global _last, _before
    found = _blocks(data)
    design, face, frame = _setting(data)
    started = time.time()
    name = "Text " + " / ".join(b["text"].split("\n")[0][:30] for b, _ in found)
    warning = ""
    parametric = design.designType == adsk.fusion.DesignTypes.ParametricDesignType

    if data["mode"] == "sharp":
        tools, places = _tools(frame, face, found)
        _clear_preview()
        if data.get("replace"):
            _take_back()
            design, face, frame = _setting(data)
        body = face.body
        body_name = body.name
        cuts = sum(1 for cut, _ in tools if cut)
        what = "%d cut%s and %d raise%s" % (cuts, "" if cuts == 1 else "s", len(tools) - cuts,
                                            "" if len(tools) - cuts == 1 else "s")
        linked = _linked(body)
        if body.isSolid and not linked:
            _before = adsk.fusion.TemporaryBRepManager.get().copy(body)
            made = wrap.apply_tools(design, body, tools, name)
            warning = _fillets(design, made, frame, places, data, body.assemblyContext)
            summary = "%s in %s" % (what, body_name)
        elif body.isSolid:
            # A linked component can't be changed here: the text goes into a
            # new body made from it, in this design, and the original is hidden.
            _before = None
            made, _ = wrap.add_body(design, _applied(body, tools), body_name + " with text")
            warning = _fillets(design, made, frame, places, data)
            _restore.append((body, body.isLightBulbOn))
            body.isLightBulbOn = False
            summary = ("%s, as a new body made from %s (now hidden), because it's in the linked "
                       "component %s, which can't be changed from here" % (what, body_name, linked))
        else:
            # A surface has no material to cut: the text goes into a new
            # surface body made from it, and the original is hidden.
            low, high = _reach(found)
            holed, shell = wrap.sharp_surface(body, frame, _areas(frame, found), tools, low, high)
            made, result = wrap.add_stitched(design, [holed, shell], name)
            warning = _fillets(design, made, frame, places, data)
            _restore.append((body, body.isLightBulbOn))
            body.isLightBulbOn = False
            summary = "%s, as a new surface body made from %s (now hidden)" % (what, body_name)
        _last = made if parametric else None
        if not parametric:
            warning = (warning + " " if warning else "") + \
                "In a direct-modelling design Replace can't take a cut back out."
    else:
        built = []
        for b, m in found:
            points = _net(frame, m, _where(b, m), m["relief"], True)
            built.append((b, wrap.build_body(points, m["nu"], m["nv"],
                                             None if data["singleFace"] else TILE_SPANS)))
        _clear_preview()
        if data.get("replace"):
            _take_back()
        _before = None
        _last = [wrap.add_to_design(design, surface, "Text " + b["text"].split("\n")[0][:40])
                 for b, surface in built]
        faces = sum(s.faces.count for _, s in built)
        summary = "%d surface%s, %d face%s" % (len(built), "" if len(built) == 1 else "s",
                                               faces, "" if faces == 1 else "s")
    return {"ok": True, "summary": summary, "warning": warning,
            "seconds": round(time.time() - started, 2)}


def _take_back():
    """Delete what the last Generate made (Replace previous), and show again
    a surface body it had hidden."""
    global _last
    for thing in _last or []:
        if thing is not None and thing.isValid:
            thing.deleteMe()
    _last = None
    while _restore:
        body, showing = _restore.pop()
        if body.isValid:
            body.isLightBulbOn = showing


_ACTIONS = {"pick": _pick, "preview": _show_preview, "hidePreview": _hide_preview,
            "generate": _generate}


class _CommandCreated(adsk.core.CommandCreatedEventHandler):
    def notify(self, args):
        _hold(args.command.execute, _CommandExecute())


class _CommandExecute(adsk.core.CommandEventHandler):
    def notify(self, args):
        _show_palette()


class _Incoming(adsk.core.HTMLEventHandler):
    def notify(self, args):
        action = _ACTIONS.get(args.action)
        if action is None:
            return
        try:
            reply = action(json.loads(args.data or "{}"))
        except _Refused as refusal:
            reply = dict(refusal.args[0], ok=False)
        except (ValueError, RuntimeError) as exc:
            reply = {"ok": False, "message": str(exc)}
        except Exception:
            app.log(traceback.format_exc())
            reply = {"ok": False, "message": traceback.format_exc(limit=1)}
        args.returnData = json.dumps(reply)


class _PaletteClosed(adsk.core.UserInterfaceGeneralEventHandler):
    def notify(self, args):
        _clear_preview()
