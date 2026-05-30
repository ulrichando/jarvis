"""Scene setup injected into Blender over the MCP socket.

Imports the FaceCap ARKit head, renames its mesh to 'FaceCap_Head' (the name
the animator drives), asserts the ARKit shape keys exist, and builds a camera
+ portrait lighting + dark world tuned to the kiosk palette. Idempotent: safe
to re-run; it won't duplicate the camera/lights and only imports the head once.

This file holds CODE as a string because it executes inside Blender's Python,
not the voice-agent venv. `install(conn)` sends it via blender_face.BlenderConnection.
"""

FACECAP_UID = "29c2a506582a4157bf970bb8721a970c"

CODE = r'''
import bpy
import mathutils

CYAN = (0.122, 0.835, 0.976)  # #1FD5F9 linear-ish

def _ensure_head():
    head = bpy.data.objects.get("FaceCap_Head")
    if head and head.type == "MESH" and head.data.shape_keys:
        return head, "exists"
    # find the FaceCap head: a mesh whose shape keys include either a literal
    # ARKit "jawOpen" or the FaceCap "target_17" alias for it.
    for o in bpy.data.objects:
        if o.type == "MESH" and o.data.shape_keys:
            names = {k.name for k in o.data.shape_keys.key_blocks}
            if "jawOpen" in names or "target_17" in names:
                o.name = "FaceCap_Head"
                return o, "renamed"
    return None, "missing"

def _world_centroid(name):
    obj = bpy.data.objects.get(name)
    if obj is None or obj.type != "MESH":
        return None
    dg = bpy.context.evaluated_depsgraph_get()
    ev = obj.evaluated_get(dg)
    mw = obj.matrix_world
    vs = [mw @ v.co for v in ev.data.vertices]
    n = len(vs) or 1
    return mathutils.Vector((sum(v.x for v in vs) / n,
                             sum(v.y for v in vs) / n,
                             sum(v.z for v in vs) / n))

def _ensure_camera():
    import math
    cam = bpy.data.objects.get("JarvisFaceCam")
    if cam is None:
        cam_data = bpy.data.cameras.new("JarvisFaceCam")
        cam = bpy.data.objects.new("JarvisFaceCam", cam_data)
        bpy.context.scene.collection.objects.link(cam)
    # drop any old Track-To; we set the matrix explicitly
    for k in list(cam.constraints):
        cam.constraints.remove(k)
    head = bpy.data.objects.get("FaceCap_Head")
    head_c = _world_centroid("FaceCap_Head")
    eyeL = _world_centroid("eyeLeft_lambert5_0")
    eyeR = _world_centroid("eyeRight_lambert5_0")
    teeth = _world_centroid("teeth_lambert5_0")

    if head and head_c and eyeL and eyeR and teeth:
        # Aim from the head's OWN axes — robust to whatever world orientation
        # the FaceCap import lands in.
        eyes_c = (eyeL + eyeR) * 0.5
        right = (eyeR - eyeL).normalized()
        up = (eyes_c - teeth).normalized()
        up = (up - right * up.dot(right)).normalized()
        forward = right.cross(up).normalized()
        if forward.dot(eyes_c - head_c) < 0:
            forward = -forward
        d = head.dimensions
        dist = max(d.x, d.y, d.z) * 2.0
        focus = eyes_c * 0.5 + teeth * 0.5
        cam_pos = focus + forward * dist
        zc = forward
        yc = up
        xc = yc.cross(zc).normalized()
        yc = zc.cross(xc).normalized()
        cam.matrix_world = mathutils.Matrix((
            (xc.x, yc.x, zc.x, cam_pos.x),
            (xc.y, yc.y, zc.y, cam_pos.y),
            (xc.z, yc.z, zc.z, cam_pos.z),
            (0, 0, 0, 1),
        ))
        cam.data.lens = 60
    elif head:
        # fallback: simple front placement if the FaceCap eye/teeth meshes
        # aren't present (e.g. a different head asset)
        c = head.matrix_world.translation
        dim = head.dimensions
        cam.location = (c.x, c.y - max(dim.x, dim.z) * 3.0, c.z + dim.z * 0.1)
        cam.rotation_euler = (1.5708, 0.0, 0.0)
        cam.data.lens = 50
    bpy.context.scene.camera = cam
    return cam

def _ensure_light(name, kind, loc, energy, color=(1, 1, 1)):
    obj = bpy.data.objects.get(name)
    if obj is None:
        ld = bpy.data.lights.new(name, kind)
        obj = bpy.data.objects.new(name, ld)
        bpy.context.scene.collection.objects.link(obj)
    obj.data.energy = energy
    obj.data.color = color
    obj.location = loc
    return obj

def _dark_world():
    scn = bpy.context.scene
    world = bpy.data.worlds.get("JarvisFaceWorld")
    if world is None:
        world = bpy.data.worlds.new("JarvisFaceWorld")
    world.use_nodes = True
    bg = world.node_tree.nodes.get("Background")
    if bg:
        bg.inputs[0].default_value = (0.01, 0.01, 0.015, 1.0)
        bg.inputs[1].default_value = 0.15
    scn.world = world

def _root(o):
    while o.parent is not None:
        o = o.parent
    return o

def _collect(o, acc):
    acc.add(o.name)
    for ch in o.children:
        _collect(ch, acc)

def _wc(name):
    o = bpy.data.objects.get(name)
    if not o or o.type != "MESH":
        return None
    dg = bpy.context.evaluated_depsgraph_get()
    ev = o.evaluated_get(dg); mw = o.matrix_world
    vs = [mw @ v.co for v in ev.data.vertices]; n = len(vs) or 1
    return mathutils.Vector((sum(v.x for v in vs)/n, sum(v.y for v in vs)/n,
                             sum(v.z for v in vs)/n))

def _basecolor_tex(nt):
    for n in nt.nodes:
        if n.type == "TEX_IMAGE" and n.image and "basecolor" in n.image.name.lower():
            return n
    return None

def _set(bsdf, name, value):
    s = bsdf.inputs.get(name)
    if s is not None:
        s.default_value = value

def _appearance(head):
    """Bake JARVIS's face look: warm golden-caramel biracial skin (texture
    detail x clean skin color), defined brows (thin dark ridge faces), and
    higher-contrast eyes. Idempotent (nodes/materials reused by name)."""
    # The eye/teeth material (original 'lambert5') is the SHARED textured
    # material — read it from the eye object, NOT the head (on a re-run the
    # head already wears JarvisSkinTex, which must not get the eye wiring).
    eye_obj = bpy.data.objects.get("eyeLeft_lambert5_0")
    lambert = (eye_obj.material_slots[0].material
               if eye_obj and eye_obj.material_slots else None)
    if lambert is None and head.material_slots:
        lambert = head.material_slots[0].material
    if lambert is None:
        return
    # --- HEAD skin material: texture detail x clean golden-caramel color ---
    skin = bpy.data.materials.get("JarvisSkinTex")
    if skin is None:
        skin = lambert.copy(); skin.name = "JarvisSkinTex"
    nt = skin.node_tree
    bsdf = next(n for n in nt.nodes if n.type == "BSDF_PRINCIPLED")
    tex = _basecolor_tex(nt)
    bc = nt.nodes.get("JarvisBC") or nt.nodes.new("ShaderNodeBrightContrast")
    bc.name = "JarvisBC"
    bc.inputs["Bright"].default_value = 0.40
    bc.inputs["Contrast"].default_value = 0.42
    mul = nt.nodes.get("JarvisTint") or nt.nodes.new("ShaderNodeMixRGB")
    mul.name = "JarvisTint"; mul.blend_type = "MULTIPLY"
    mul.inputs[0].default_value = 1.0
    mul.inputs[2].default_value = (0.52, 0.265, 0.125, 1.0)  # warm golden-caramel
    if tex:
        nt.links.new(tex.outputs["Color"], bc.inputs["Color"])
    nt.links.new(bc.outputs["Color"], mul.inputs[1])
    for l in list(bsdf.inputs["Base Color"].links):
        nt.links.remove(l)
    nt.links.new(mul.outputs[0], bsdf.inputs["Base Color"])
    _set(bsdf, "Coat Weight", 0.0)
    _set(bsdf, "Roughness", 0.6)
    _set(bsdf, "Specular IOR Level", 0.3)
    _set(bsdf, "Subsurface Weight", 0.06)
    _set(bsdf, "Subsurface Radius", (0.36, 0.18, 0.12))
    # --- brow material (dark warm brown, matte) ---
    brow = bpy.data.materials.get("JarvisBrow")
    if brow is None:
        brow = bpy.data.materials.new("JarvisBrow"); brow.use_nodes = True
    bb = next(n for n in brow.node_tree.nodes if n.type == "BSDF_PRINCIPLED")
    bb.inputs["Base Color"].default_value = (0.045, 0.026, 0.016, 1.0)
    bb.inputs["Roughness"].default_value = 0.88
    _set(bb, "Coat Weight", 0.0)
    me = head.data
    me.materials.clear(); me.materials.append(skin); me.materials.append(brow)
    # --- EYE contrast (lambert5, shared by eyes/teeth) so iris/pupil define ---
    ent = lambert.node_tree
    eb = next(n for n in ent.nodes if n.type == "BSDF_PRINCIPLED")
    etex = _basecolor_tex(ent)
    ebc = ent.nodes.get("JarvisEyeBC") or ent.nodes.new("ShaderNodeBrightContrast")
    ebc.name = "JarvisEyeBC"
    ebc.inputs["Bright"].default_value = 0.0
    ebc.inputs["Contrast"].default_value = 0.85
    if etex:
        ent.links.new(etex.outputs["Color"], ebc.inputs["Color"])
    for l in list(eb.inputs["Base Color"].links):
        ent.links.remove(l)
    ent.links.new(ebc.outputs["Color"], eb.inputs["Base Color"])
    _set(eb, "Coat Weight", 0.0)
    _set(eb, "Roughness", 0.45)
    # --- brow face band on the ridge (thin, follows geometry) ---
    hc = _wc("FaceCap_Head"); eyeL = _wc("eyeLeft_lambert5_0")
    eyeR = _wc("eyeRight_lambert5_0"); teeth = _wc("teeth_lambert5_0")
    for poly in me.polygons:
        poly.material_index = 0
    if hc and eyeL and eyeR and teeth:
        right = (eyeR - eyeL).normalized()
        up = (((eyeL + eyeR) * 0.5) - teeth).normalized()
        up = (up - right * up.dot(right)).normalized()
        fwd = right.cross(up).normalized()
        if fwd.dot(((eyeL + eyeR) * 0.5) - hc) < 0:
            fwd = -fwd
        mw = head.matrix_world
        for poly in me.polygons:
            c = mw @ poly.center
            for ec in (eyeL, eyeR):
                d = c - ec; u = d.dot(up); rt = d.dot(right); fc = d.dot(fwd)
                if 0.009 < u < 0.0150 and abs(rt) < 0.017 and fc > -0.002:
                    poly.material_index = 1
                    break
    # slightly open the eyes (ARKit eyeWide = target_17/18)
    sk = head.data.shape_keys.key_blocks
    for t in ("target_17", "target_18"):
        if t in sk:
            sk[t].value = 1.0

def setup():
    head, status = _ensure_head()
    if head is None:
        print("RESULT: NO_HEAD")
        return
    # Keep the FaceCap head's whole hierarchy (head/teeth/eyes) visible; hide
    # every OTHER mesh (the female sculpt, the default cube) without deleting.
    keep = set()
    _collect(_root(head), keep)
    for o in bpy.data.objects:
        if o.type == "MESH" and o.name not in keep:
            o.hide_render = True
            o.hide_viewport = True
    # smooth-shade the head
    if head.type == "MESH":
        for p in head.data.polygons:
            p.use_smooth = True
    _ensure_camera()
    # tuned portrait lighting (low energies — the face is a small close-up;
    # higher values blow the skin to white). Cool rim for edge separation.
    _ensure_light("JarvisKey",  "AREA", (1.2, -1.6, 1.2), 24.0)
    _ensure_light("JarvisFill", "AREA", (-1.4, -1.2, 0.6), 9.0)
    _ensure_light("JarvisRim",  "AREA", (0.0, 1.6, 1.4), 10.0, (0.55, 0.8, 1.0))
    _dark_world()
    # Standard view transform — AgX desaturates skin tones toward pale.
    bpy.context.scene.view_settings.view_transform = 'Standard'
    # Bake the face look (skin / brows / eyes).
    _appearance(head)
    # report the resolved shape-key NAMES (literal ARKit or target_N alias)
    kb = head.data.shape_keys.key_blocks
    avail = {k.name for k in kb}
    aliases = {"jawOpen": "target_24", "mouthClose": "target_28",
               "mouthFunnel": "target_29", "mouthPucker": "target_30"}
    resolved = {}
    for want in ("jawOpen", "mouthClose", "mouthFunnel", "mouthPucker"):
        if want in avail:
            resolved[want] = want
        elif aliases[want] in avail:
            resolved[want] = aliases[want]
    # neutral start: zero the shapes the animator drives
    for keyname in resolved.values():
        kb[keyname].value = 0.0
    print("RESULT: OK status=%s shapes=%d resolved=%s name=%s"
          % (status, len(kb), resolved, head.name))

setup()
'''


def install(conn):
    """Send the scene-setup CODE into Blender via a BlenderConnection.

    `conn` is an animators.blender_face.BlenderConnection. Returns the raw
    result dict from the MCP addon (contains the printed RESULT line).
    """
    return conn.send("execute_code", {"code": CODE})
