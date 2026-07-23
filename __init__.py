bl_info = {
    "name": "UE Chaos Vehicle Prep",
    "author": "Lexikonn",
    "version": (1, 1, 0),
    "blender": (4, 2, 0),
    "location": "3D Viewport > Sidebar > UE Chaos",
    "description": "Prepares a vehicle mesh and rigid rig for Unreal Engine Chaos Vehicles",
    "category": "Rigging",
}

import bpy
import bmesh
import json
import math
import os
import re
import time
from mathutils import Matrix, Vector
from bpy.props import BoolProperty, EnumProperty, FloatProperty, PointerProperty, StringProperty
from bpy.types import Operator, Panel, PropertyGroup
from bpy_extras.io_utils import ExportHelper
from .translations import TRANSLATIONS


def tr(message):
    return bpy.app.translations.pgettext_iface(message)


def trf(message, **values):
    return tr(message).format(**values)


ROLE_ORDER = (
    "body",
    "wheel_fl",
    "wheel_fr",
    "wheel_rl",
    "wheel_rr",
    "door_fl",
    "door_fr",
    "door_rl",
    "door_rr",
    "steering",
)

ROLE_LABELS = {
    "body": "Body / chassis",
    "wheel_fl": "Front Left Wheel",
    "wheel_fr": "Front Right Wheel",
    "wheel_rl": "Rear Left Wheel",
    "wheel_rr": "Rear Right Wheel",
    "door_fl": "Front Left Door",
    "door_fr": "Front Right Door",
    "door_rl": "Rear Left Door",
    "door_rr": "Rear Right Door",
    "steering": "Steering Wheel",
}


def role_label(role):
    return tr(ROLE_LABELS[role])

BONE_NAMES = {
    "body": "root",
    "wheel_fl": "wheel_fl",
    "wheel_fr": "wheel_fr",
    "wheel_rl": "wheel_rl",
    "wheel_rr": "wheel_rr",
    "door_fl": "door_fl",
    "door_fr": "door_fr",
    "door_rl": "door_rl",
    "door_rr": "door_rr",
    "steering": "steering_wheel",
}

CANONICAL_OBJECT_NAMES = {
    "body": "Body",
    "wheel_fl": "Wheel_FL",
    "wheel_fr": "Wheel_FR",
    "wheel_rl": "Wheel_RL",
    "wheel_rr": "Wheel_RR",
    "door_fl": "Door_FL",
    "door_fr": "Door_FR",
    "door_rl": "Door_RL",
    "door_rr": "Door_RR",
    "steering": "SteeringWheel",
}

ALIASES = {
    "body": (
        "body", "carbody", "vehiclebody", "chassis", "shell", "carshell", "frame", "mainbody",
    ),
    "wheel_fl": (
        "wheelfl", "wheellf", "frontleftwheel", "wheelfrontleft", "tirefl", "tyrefl", "frontlefttire", "frontlefttyre", "fleftwheel",
    ),
    "wheel_fr": (
        "wheelfr", "wheelrf", "frontrightwheel", "wheelfrontright", "tirefr", "tyrefr", "frontrighttire", "frontrighttyre", "frightwheel",
    ),
    "wheel_rl": (
        "wheelrl", "wheellr", "wheelbl", "wheellb", "rearleftwheel", "backleftwheel", "wheelrearleft", "wheelbackleft", "tirerl", "tyrerl", "tirebl", "tyrebl",
    ),
    "wheel_rr": (
        "wheelrr", "wheelbr", "wheelrb", "rearrightwheel", "backrightwheel", "wheelrearright", "wheelbackright", "tirerr", "tyrerr", "tirebr", "tyrebr",
    ),
    "door_fl": (
        "doorfl", "doorlf", "frontleftdoor", "doorfrontleft", "fleftdoor",
    ),
    "door_fr": (
        "doorfr", "doorrf", "frontrightdoor", "doorfrontright", "frightdoor",
    ),
    "door_rl": (
        "doorrl", "doorlr", "doorbl", "doorlb", "rearleftdoor", "backleftdoor", "doorrearleft", "doorbackleft",
    ),
    "door_rr": (
        "doorrr", "doorbr", "doorrb", "rearrightdoor", "backrightdoor", "doorrearright", "doorbackright",
    ),
    "steering": (
        "steering", "steer", "steeringwheel", "steerwheel", "volant",
    ),
}

REQUIRED_ROLES = ("body", "wheel_fl", "wheel_fr", "wheel_rl", "wheel_rr")
OPTIONAL_ROLES = ("door_fl", "door_fr", "door_rl", "door_rr", "steering")


def mesh_object_poll(self, obj):
    return obj is not None and obj.type == "MESH"


def normalize_name(value):
    return re.sub(r"[^a-z0-9]+", "", value.lower())


def safe_name(value):
    cleaned = re.sub(r"[^A-Za-z0-9_]+", "_", value.strip())
    cleaned = re.sub(r"_+", "_", cleaned).strip("_")
    return cleaned or "Vehicle"


def object_world_corners(obj):
    return [obj.matrix_world @ Vector(corner) for corner in obj.bound_box]


def object_world_bounds(obj):
    corners = object_world_corners(obj)
    minimum = Vector((min(v.x for v in corners), min(v.y for v in corners), min(v.z for v in corners)))
    maximum = Vector((max(v.x for v in corners), max(v.y for v in corners), max(v.z for v in corners)))
    return minimum, maximum


def object_world_center(obj):
    minimum, maximum = object_world_bounds(obj)
    return (minimum + maximum) * 0.5


def combined_world_bounds(objects):
    corners = []
    for obj in objects:
        corners.extend(object_world_corners(obj))
    minimum = Vector((min(v.x for v in corners), min(v.y for v in corners), min(v.z for v in corners)))
    maximum = Vector((max(v.x for v in corners), max(v.y for v in corners), max(v.z for v in corners)))
    return minimum, maximum


def candidate_meshes(context, settings):
    if settings.scope == "SELECTED":
        result = [obj for obj in context.selected_objects if obj.type == "MESH"]
    elif settings.scope == "SCENE":
        result = [obj for obj in context.scene.objects if obj.type == "MESH" and not obj.get("uechaos_backup")]
    else:
        collection = settings.source_collection
        if collection is None and context.active_object is not None:
            collection = context.active_object.users_collection[0] if context.active_object.users_collection else None
        if collection is None:
            result = [obj for obj in context.selected_objects if obj.type == "MESH"]
        else:
            result = [obj for obj in collection.all_objects if obj.type == "MESH" and not obj.get("uechaos_backup")]
    unique = []
    seen = set()
    for obj in result:
        if obj.as_pointer() not in seen:
            seen.add(obj.as_pointer())
            unique.append(obj)
    return unique


def score_name(role, obj_name):
    normalized = normalize_name(obj_name)
    best = 0
    for alias in ALIASES[role]:
        if normalized == alias:
            best = max(best, 100)
        elif normalized.startswith(alias) or normalized.endswith(alias):
            best = max(best, 85)
        elif alias in normalized:
            best = max(best, 70)
    return best


def find_best_candidate(role, candidates, used):
    scored = []
    for obj in candidates:
        if obj.as_pointer() in used:
            continue
        score = score_name(role, obj.name)
        if score > 0:
            scored.append((score, obj))
    scored.sort(key=lambda item: (item[0], len(item[1].data.vertices)), reverse=True)
    return scored[0][1] if scored and scored[0][0] >= 70 else None


def auto_detect_parts(context, settings):
    candidates = candidate_meshes(context, settings)
    used = set()
    assignments = {}
    for role in ROLE_ORDER:
        obj = find_best_candidate(role, candidates, used)
        if obj is not None:
            assignments[role] = obj
            used.add(obj.as_pointer())
    if "body" not in assignments:
        remaining = [obj for obj in candidates if obj.as_pointer() not in used]
        if remaining:
            body = max(remaining, key=lambda obj: len(obj.data.vertices))
            assignments["body"] = body
            used.add(body.as_pointer())
    for role, obj in assignments.items():
        setattr(settings, role, obj)
    missing = [role for role in REQUIRED_ROLES if getattr(settings, role) is None]
    detected = len(assignments)
    return detected, missing


def selected_role_objects(settings):
    result = {}
    for role in ROLE_ORDER:
        obj = getattr(settings, role)
        if obj is not None:
            result[role] = obj
    return result


def source_objects(context, settings):
    roles = selected_role_objects(settings)
    result = list(dict.fromkeys(roles.values()))
    if settings.include_unassigned:
        for obj in candidate_meshes(context, settings):
            if obj not in result:
                result.append(obj)
    return [obj for obj in result if obj.type == "MESH" and not obj.get("uechaos_backup")]


def unit_to_cm(scene):
    if scene.unit_settings.system == "NONE":
        return 100.0
    return scene.unit_settings.scale_length * 100.0


def wheel_radius_world(obj):
    minimum, maximum = object_world_bounds(obj)
    dimensions = maximum - minimum
    return (abs(dimensions.y) + abs(dimensions.z)) * 0.25


def validate_settings(context, settings):
    errors = []
    warnings = []
    roles = selected_role_objects(settings)
    for role in REQUIRED_ROLES:
        obj = getattr(settings, role)
        if obj is None:
            errors.append(trf("Missing: {part}", part=role_label(role)))
        elif obj.type != "MESH":
            errors.append(trf("{part} is not a mesh", part=role_label(role)))
    pointers = [obj.as_pointer() for obj in roles.values() if obj is not None]
    if len(pointers) != len(set(pointers)):
        errors.append(tr("The same object is assigned to multiple parts"))
    for role, obj in roles.items():
        if len(obj.data.vertices) == 0:
            errors.append(trf("{part} has no vertices", part=role_label(role)))
        determinant = obj.matrix_world.to_3x3().determinant()
        if determinant < 0.0:
            warnings.append(trf("{part} has negative scale; the tool will fix it", part=role_label(role)))
        scale = obj.matrix_world.to_scale()
        if max(scale) - min(scale) > 0.001:
            warnings.append(trf("{part} has non-uniform scale; the tool will apply it", part=role_label(role)))
    if not errors:
        wheel_centers = [object_world_center(getattr(settings, role)) for role in ("wheel_fl", "wheel_fr", "wheel_rl", "wheel_rr")]
        min_distance = min((wheel_centers[i] - wheel_centers[j]).length for i in range(4) for j in range(i + 1, 4))
        if min_distance < 0.001:
            errors.append(tr("At least two wheels have the same center"))
        front = (wheel_centers[0] + wheel_centers[1]) * 0.5
        rear = (wheel_centers[2] + wheel_centers[3]) * 0.5
        if (front - rear).length < 0.01:
            errors.append(tr("Cannot determine vehicle direction from wheel positions"))
        left = (wheel_centers[0] + wheel_centers[2]) * 0.5
        right = (wheel_centers[1] + wheel_centers[3]) * 0.5
        if (left - right).length < 0.01:
            errors.append(tr("Cannot determine left and right sides from wheel positions"))
    objects = source_objects(context, settings)
    if not objects:
        errors.append(tr("No meshes found in the selected scope"))
    if objects:
        minimum, maximum = combined_world_bounds(objects)
        dimensions = maximum - minimum
        length_cm = max(abs(dimensions.x), abs(dimensions.y)) * unit_to_cm(context.scene)
        if length_cm < 100.0 or length_cm > 3000.0:
            warnings.append(trf("Vehicle size looks suspicious: {size:.1f} cm", size=length_cm))
    return errors, warnings


def make_report(errors, warnings, success_text=""):
    lines = []
    if errors:
        lines.append(tr("ERRORS"))
        lines.extend(f"• {line}" for line in errors)
    if warnings:
        if lines:
            lines.append("")
        lines.append(tr("WARNINGS"))
        lines.extend(f"• {line}" for line in warnings)
    if success_text:
        if lines:
            lines.append("")
        lines.append(success_text)
    if not lines:
        lines.append(tr("Settings are valid"))
    return "\n".join(lines)


def set_active_only(context, obj):
    if context.object is not None and context.object.mode != "OBJECT":
        bpy.ops.object.mode_set(mode="OBJECT")
    bpy.ops.object.select_all(action="DESELECT")
    obj.select_set(True)
    context.view_layer.objects.active = obj


def make_mesh_data_unique(objects):
    for obj in objects:
        if obj.data.users > 1:
            obj.data = obj.data.copy()


def apply_object_modifiers(context, objects):
    depsgraph = context.evaluated_depsgraph_get()
    for obj in objects:
        if not obj.modifiers:
            continue
        evaluated = obj.evaluated_get(depsgraph)
        new_mesh = bpy.data.meshes.new_from_object(evaluated, preserve_all_data_layers=True, depsgraph=depsgraph)
        old_mesh = obj.data
        obj.data = new_mesh
        obj.modifiers.clear()
        if old_mesh.users == 0:
            bpy.data.meshes.remove(old_mesh)


def apply_transforms(context, objects):
    if context.object is not None and context.object.mode != "OBJECT":
        bpy.ops.object.mode_set(mode="OBJECT")
    make_mesh_data_unique(objects)
    bpy.ops.object.select_all(action="DESELECT")
    for obj in objects:
        obj.select_set(True)
    context.view_layer.objects.active = objects[0]
    bpy.ops.object.transform_apply(location=True, rotation=True, scale=True, properties=True)


def fix_mesh_data(obj, merge_by_distance, merge_distance, clear_custom_normals):
    mesh = obj.data
    bm = bmesh.new()
    bm.from_mesh(mesh)
    if merge_by_distance and bm.verts:
        bmesh.ops.remove_doubles(bm, verts=list(bm.verts), dist=merge_distance)
    if bm.faces:
        bmesh.ops.recalc_face_normals(bm, faces=list(bm.faces))
    bm.to_mesh(mesh)
    bm.free()
    mesh.validate(clean_customdata=clear_custom_normals)
    mesh.update()


def create_backup(context, settings, objects, role_map):
    if not settings.create_backup:
        return None
    name = safe_name(settings.vehicle_name)
    stamp = time.strftime("%Y%m%d_%H%M%S")
    collection = bpy.data.collections.new(f"UECHAOS_BACKUP_{name}_{stamp}")
    context.scene.collection.children.link(collection)
    collection["uechaos_backup_vehicle"] = name
    for obj in objects:
        duplicate = obj.copy()
        duplicate.data = obj.data.copy()
        collection.objects.link(duplicate)
        duplicate.matrix_world = obj.matrix_world.copy()
        duplicate.hide_render = True
        duplicate.hide_set(True)
        duplicate["uechaos_backup"] = True
        duplicate["uechaos_source_name"] = obj.name
        for role, mapped in role_map.items():
            if mapped == obj:
                duplicate["uechaos_role"] = role
                break
    settings.backup_collection_name = collection.name
    return collection


def rotate_objects_about_pivot(objects, angle, pivot):
    transform = Matrix.Translation(pivot) @ Matrix.Rotation(angle, 4, "Z") @ Matrix.Translation(-pivot)
    for obj in objects:
        obj.matrix_world = transform @ obj.matrix_world


def translate_objects(objects, offset):
    transform = Matrix.Translation(offset)
    for obj in objects:
        obj.matrix_world = transform @ obj.matrix_world


def align_and_center(settings, objects):
    wheel_fl = object_world_center(settings.wheel_fl)
    wheel_fr = object_world_center(settings.wheel_fr)
    wheel_rl = object_world_center(settings.wheel_rl)
    wheel_rr = object_world_center(settings.wheel_rr)
    if settings.auto_align_forward:
        front = (wheel_fl + wheel_fr) * 0.5
        rear = (wheel_rl + wheel_rr) * 0.5
        direction = front - rear
        current_angle = math.atan2(direction.y, direction.x)
        target_angle = -math.pi * 0.5
        minimum, maximum = combined_world_bounds(objects)
        pivot = (minimum + maximum) * 0.5
        rotate_objects_about_pivot(objects, target_angle - current_angle, pivot)
    if settings.center_and_ground:
        minimum, maximum = combined_world_bounds(objects)
        offset = Vector((-(minimum.x + maximum.x) * 0.5, -(minimum.y + maximum.y) * 0.5, -minimum.z))
        translate_objects(objects, offset)


def door_pivot(settings, role, obj):
    if settings.door_pivot_mode == "ORIGIN":
        return obj.matrix_world.translation.copy()
    minimum, maximum = object_world_bounds(obj)
    return Vector(((minimum.x + maximum.x) * 0.5, minimum.y, (minimum.z + maximum.z) * 0.5))


def part_pivots(settings):
    pivots = {
        "wheel_fl": object_world_center(settings.wheel_fl),
        "wheel_fr": object_world_center(settings.wheel_fr),
        "wheel_rl": object_world_center(settings.wheel_rl),
        "wheel_rr": object_world_center(settings.wheel_rr),
    }
    for role in ("door_fl", "door_fr", "door_rl", "door_rr"):
        obj = getattr(settings, role)
        if obj is not None and settings.create_door_bones:
            pivots[role] = door_pivot(settings, role, obj)
    if settings.steering is not None and settings.create_steering_bone:
        pivots["steering"] = settings.steering.matrix_world.translation.copy()
    return pivots


def clear_vertex_groups(obj):
    while obj.vertex_groups:
        obj.vertex_groups.remove(obj.vertex_groups[0])


def assign_rigid_group(obj, group_name, clear_existing):
    if clear_existing:
        clear_vertex_groups(obj)
    group = obj.vertex_groups.get(group_name)
    if group is None:
        group = obj.vertex_groups.new(name=group_name)
    indices = [vertex.index for vertex in obj.data.vertices]
    if indices:
        group.add(indices, 1.0, "REPLACE")


def create_armature(context, settings, pivots, dimensions):
    name = safe_name(settings.vehicle_name)
    armature_data = bpy.data.armatures.new(f"SKEL_{name}")
    armature = bpy.data.objects.new(f"SKEL_{name}", armature_data)
    context.collection.objects.link(armature)
    armature.show_in_front = True
    armature["uechaos_generated"] = True
    armature["uechaos_vehicle"] = name
    set_active_only(context, armature)
    bpy.ops.object.mode_set(mode="EDIT")
    bone_length = max(max(dimensions.x, dimensions.y) * 0.04, dimensions.z * 0.08, 0.05)
    root = armature_data.edit_bones.new("root")
    root.head = Vector((0.0, 0.0, 0.0))
    root.tail = Vector((0.0, -bone_length, 0.0))
    root.align_roll(Vector((0.0, 0.0, 1.0)))
    root.use_deform = True
    for role in ("wheel_fl", "wheel_fr", "wheel_rl", "wheel_rr", "door_fl", "door_fr", "door_rl", "door_rr", "steering"):
        if role not in pivots:
            continue
        bone = armature_data.edit_bones.new(BONE_NAMES[role])
        bone.head = pivots[role]
        bone.tail = pivots[role] + Vector((0.0, -bone_length, 0.0))
        bone.align_roll(Vector((0.0, 0.0, 1.0)))
        bone.parent = root
        bone.use_connect = False
        bone.use_deform = True
    bpy.ops.object.mode_set(mode="OBJECT")
    return armature


def join_meshes(context, settings, objects, role_map, armature):
    object_roles = {obj.as_pointer(): role for role, obj in role_map.items()}
    for obj in objects:
        role = object_roles.get(obj.as_pointer(), "body")
        if role in OPTIONAL_ROLES:
            if role.startswith("door") and not settings.create_door_bones:
                role = "body"
            if role == "steering" and not settings.create_steering_bone:
                role = "body"
        assign_rigid_group(obj, BONE_NAMES.get(role, "root"), settings.clear_vertex_groups)
    bpy.ops.object.select_all(action="DESELECT")
    for obj in objects:
        obj.select_set(True)
    active = settings.body if settings.body in objects else objects[0]
    context.view_layer.objects.active = active
    bpy.ops.object.join()
    final_mesh = context.view_layer.objects.active
    name = safe_name(settings.vehicle_name)
    final_mesh.name = f"SK_{name}"
    final_mesh.data.name = f"SK_{name}_Mesh"
    final_mesh["uechaos_generated"] = True
    final_mesh["uechaos_vehicle"] = name
    modifier = final_mesh.modifiers.new(name="Armature", type="ARMATURE")
    modifier.object = armature
    modifier.use_deform_preserve_volume = False
    final_mesh.parent = armature
    final_mesh.matrix_parent_inverse = armature.matrix_world.inverted()
    return final_mesh


def rename_source_objects(role_map):
    for role, obj in role_map.items():
        obj.name = CANONICAL_OBJECT_NAMES[role]
        obj.data.name = f"{CANONICAL_OBJECT_NAMES[role]}_Mesh"


def generated_objects(settings):
    mesh = bpy.data.objects.get(settings.generated_mesh_name) if settings.generated_mesh_name else None
    armature = bpy.data.objects.get(settings.generated_armature_name) if settings.generated_armature_name else None
    if mesh is None or armature is None:
        name = safe_name(settings.vehicle_name)
        for obj in bpy.data.objects:
            if obj.get("uechaos_generated") and obj.get("uechaos_vehicle") == name:
                if obj.type == "MESH":
                    mesh = obj
                elif obj.type == "ARMATURE":
                    armature = obj
    return mesh, armature


def delete_object_and_data(obj):
    data = obj.data
    bpy.data.objects.remove(obj, do_unlink=True)
    if data is not None and data.users == 0:
        if isinstance(data, bpy.types.Mesh):
            bpy.data.meshes.remove(data)
        elif isinstance(data, bpy.types.Armature):
            bpy.data.armatures.remove(data)


def export_sidecar(filepath, settings, mesh, armature):
    scale_cm = unit_to_cm(bpy.context.scene)
    wheel_data = {}
    for role in ("wheel_fl", "wheel_fr", "wheel_rl", "wheel_rr"):
        bone = armature.data.bones.get(BONE_NAMES[role])
        radius = float(mesh.get(f"{role}_radius_cm", 0.0))
        center_blender = bone.head_local * scale_cm if bone is not None else Vector((0.0, 0.0, 0.0))
        center = Vector((-center_blender.y, center_blender.x, center_blender.z))
        wheel_data[role] = {
            "bone": BONE_NAMES[role],
            "radius_cm": round(radius, 4),
            "center_cm": [round(center.x, 4), round(center.y, 4), round(center.z, 4)],
        }
    data = {
        "author": "Lexikonn",
        "vehicle_name": settings.vehicle_name,
        "skeletal_mesh": mesh.name,
        "armature": armature.name,
        "ue_forward": "+X",
        "ue_up": "+Z",
        "root_bone": "root",
        "wheel_order": ["wheel_fl", "wheel_fr", "wheel_rl", "wheel_rr"],
        "wheels": wheel_data,
        "optional_bones": [bone.name for bone in armature.data.bones if bone.name not in {"root", "wheel_fl", "wheel_fr", "wheel_rl", "wheel_rr"}],
        "ue_import": {
            "import_as_skeletal": True,
            "create_physics_asset": True,
            "wheel_bones": ["wheel_fl", "wheel_fr", "wheel_rl", "wheel_rr"],
        },
    }
    json_path = os.path.splitext(filepath)[0] + "_ChaosSetup.json"
    with open(json_path, "w", encoding="utf-8") as handle:
        json.dump(data, handle, ensure_ascii=False, indent=2)
    return json_path


class UECHAOS_PG_ChaosVehicleSettings(PropertyGroup):
    vehicle_name: StringProperty(name="Vehicle Name", default="Vehicle")
    scope: EnumProperty(
        name="Search Scope",
        items=(
            ("COLLECTION", "Collection", "Use objects from the selected collection"),
            ("SELECTED", "Selected", "Use selected objects only"),
            ("SCENE", "Entire Scene", "Use all meshes in the scene"),
        ),
        default="COLLECTION",
    )
    source_collection: PointerProperty(name="Source Collection", type=bpy.types.Collection)
    body: PointerProperty(name="Body", type=bpy.types.Object, poll=mesh_object_poll)
    wheel_fl: PointerProperty(name="Wheel FL", type=bpy.types.Object, poll=mesh_object_poll)
    wheel_fr: PointerProperty(name="Wheel FR", type=bpy.types.Object, poll=mesh_object_poll)
    wheel_rl: PointerProperty(name="Wheel RL / BL", type=bpy.types.Object, poll=mesh_object_poll)
    wheel_rr: PointerProperty(name="Wheel RR / BR", type=bpy.types.Object, poll=mesh_object_poll)
    door_fl: PointerProperty(name="Door FL", type=bpy.types.Object, poll=mesh_object_poll)
    door_fr: PointerProperty(name="Door FR", type=bpy.types.Object, poll=mesh_object_poll)
    door_rl: PointerProperty(name="Door RL / BL", type=bpy.types.Object, poll=mesh_object_poll)
    door_rr: PointerProperty(name="Door RR / BR", type=bpy.types.Object, poll=mesh_object_poll)
    steering: PointerProperty(name="Steering Wheel", type=bpy.types.Object, poll=mesh_object_poll)
    include_unassigned: BoolProperty(name="Include unassigned meshes", default=True)
    auto_align_forward: BoolProperty(name="Automatically align forward direction", default=True)
    center_and_ground: BoolProperty(name="Center and place on Z=0", default=True)
    apply_all_transforms: BoolProperty(name="Apply All Transforms", default=True)
    apply_modifiers: BoolProperty(name="Apply Modifiers", default=True)
    recalc_normals: BoolProperty(name="Recalculate Normals Outside", default=True)
    clear_custom_normals: BoolProperty(name="Clear Custom Normals", default=True)
    merge_by_distance: BoolProperty(name="Merge by Distance", default=False)
    merge_distance: FloatProperty(name="Merge Distance", default=0.0001, min=0.0, precision=6)
    clear_vertex_groups: BoolProperty(name="Replace Existing Weights", default=True)
    create_door_bones: BoolProperty(name="Create Door Bones", default=True)
    create_steering_bone: BoolProperty(name="Create Steering Wheel Bone", default=True)
    door_pivot_mode: EnumProperty(
        name="Door Pivot",
        items=(
            ("AUTO", "Automatic Hinge", "Use the front vertical edge of the door"),
            ("ORIGIN", "Object Origin", "Use the current door origin"),
        ),
        default="AUTO",
    )
    create_backup: BoolProperty(name="Create Hidden Backup", default=True)
    copy_textures: BoolProperty(name="Copy Textures Next to FBX", default=True)
    generated_mesh_name: StringProperty(default="")
    generated_armature_name: StringProperty(default="")
    backup_collection_name: StringProperty(default="")
    last_report: StringProperty(default="")
    last_status: StringProperty(default="")


class UECHAOS_OT_AutoDetect(Operator):
    bl_idname = "uechaos.auto_detect"
    bl_label = "Automatically Detect Parts"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        settings = context.scene.ue_chaos_vehicle_prep
        detected, missing = auto_detect_parts(context, settings)
        errors, warnings = validate_settings(context, settings)
        settings.last_report = make_report(errors, warnings, trf("Assignments found: {count}", count=detected))
        if missing:
            self.report({"WARNING"}, tr("Some required parts were not found"))
            bpy.ops.uechaos.map_parts("INVOKE_DEFAULT")
        else:
            self.report({"INFO"}, tr("Parts were detected and assigned"))
        return {"FINISHED"}


class UECHAOS_OT_MapParts(Operator):
    bl_idname = "uechaos.map_parts"
    bl_label = "Vehicle Part Assignment"

    def invoke(self, context, event):
        return context.window_manager.invoke_props_dialog(self, width=620)

    def draw(self, context):
        settings = context.scene.ue_chaos_vehicle_prep
        layout = self.layout
        layout.label(text=tr("Select parts manually. Object fields support the eyedropper."), icon="EYEDROPPER")
        box = layout.box()
        for role in REQUIRED_ROLES:
            box.prop(settings, role, text=role_label(role))
        optional = layout.box()
        optional.label(text=tr("Optional Moving Parts"))
        for role in OPTIONAL_ROLES:
            optional.prop(settings, role, text=role_label(role))
        errors, warnings = validate_settings(context, settings)
        if errors:
            report = layout.box()
            report.label(text=tr("Required setup is incomplete"), icon="ERROR")
            for line in errors:
                report.label(text=line)

    def execute(self, context):
        settings = context.scene.ue_chaos_vehicle_prep
        errors, warnings = validate_settings(context, settings)
        settings.last_report = make_report(errors, warnings)
        if errors:
            self.report({"ERROR"}, tr("Assignment is still incomplete"))
            return {"CANCELLED"}
        self.report({"INFO"}, tr("Assignment is complete"))
        return {"FINISHED"}


class UECHAOS_OT_Validate(Operator):
    bl_idname = "uechaos.validate"
    bl_label = "Validate Vehicle"

    def invoke(self, context, event):
        settings = context.scene.ue_chaos_vehicle_prep
        errors, warnings = validate_settings(context, settings)
        settings.last_report = make_report(errors, warnings)
        return context.window_manager.invoke_props_dialog(self, width=620)

    def draw(self, context):
        settings = context.scene.ue_chaos_vehicle_prep
        layout = self.layout
        for line in settings.last_report.splitlines():
            if line == tr("ERRORS"):
                layout.label(text=line, icon="ERROR")
            elif line == tr("WARNINGS"):
                layout.label(text=line, icon="INFO")
            else:
                layout.label(text=line)

    def execute(self, context):
        return {"FINISHED"}


class UECHAOS_OT_BuildRig(Operator):
    bl_idname = "uechaos.build_rig"
    bl_label = "Build UE Chaos Rig"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        settings = context.scene.ue_chaos_vehicle_prep
        errors, warnings = validate_settings(context, settings)
        if errors:
            settings.last_report = make_report(errors, warnings)
            self.report({"ERROR"}, tr("Required parts are missing or the setup is invalid"))
            bpy.ops.uechaos.map_parts("INVOKE_DEFAULT")
            return {"CANCELLED"}
        existing_mesh, existing_armature = generated_objects(settings)
        if existing_mesh is not None or existing_armature is not None:
            self.report({"ERROR"}, tr("A generated rig already exists for this vehicle. Restore the backup or change the vehicle name first."))
            return {"CANCELLED"}
        role_map = selected_role_objects(settings)
        objects = source_objects(context, settings)
        create_backup(context, settings, objects, role_map)
        align_and_center(settings, objects)
        pivots = part_pivots(settings)
        wheel_radii = {role: wheel_radius_world(getattr(settings, role)) for role in ("wheel_fl", "wheel_fr", "wheel_rl", "wheel_rr")}
        rename_source_objects(role_map)
        if settings.apply_modifiers:
            apply_object_modifiers(context, objects)
        if settings.apply_all_transforms:
            apply_transforms(context, objects)
        if settings.recalc_normals:
            for obj in objects:
                fix_mesh_data(obj, settings.merge_by_distance, settings.merge_distance, settings.clear_custom_normals)
        minimum, maximum = combined_world_bounds(objects)
        dimensions = maximum - minimum
        armature = create_armature(context, settings, pivots, dimensions)
        final_mesh = join_meshes(context, settings, objects, role_map, armature)
        if settings.recalc_normals:
            fix_mesh_data(final_mesh, False, settings.merge_distance, settings.clear_custom_normals)
        scale_cm = unit_to_cm(context.scene)
        final_mesh["vehicle_length_cm"] = max(abs(dimensions.x), abs(dimensions.y)) * scale_cm
        final_mesh["vehicle_width_cm"] = min(abs(dimensions.x), abs(dimensions.y)) * scale_cm
        final_mesh["vehicle_height_cm"] = abs(dimensions.z) * scale_cm
        for role, radius in wheel_radii.items():
            final_mesh[f"{role}_radius_cm"] = radius * scale_cm
        settings.generated_mesh_name = final_mesh.name
        settings.generated_armature_name = armature.name
        settings.body = final_mesh
        for role in REQUIRED_ROLES[1:] + OPTIONAL_ROLES:
            setattr(settings, role, None)
        settings.last_status = trf("Done: {mesh} + {armature}", mesh=final_mesh.name, armature=armature.name)
        settings.last_report = make_report([], warnings, settings.last_status)
        set_active_only(context, final_mesh)
        self.report({"INFO"}, settings.last_status)
        return {"FINISHED"}


class UECHAOS_OT_RestoreBackup(Operator):
    bl_idname = "uechaos.restore_backup"
    bl_label = "Restore Last Backup"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        settings = context.scene.ue_chaos_vehicle_prep
        collection = bpy.data.collections.get(settings.backup_collection_name)
        if collection is None:
            self.report({"ERROR"}, tr("Backup collection was not found"))
            return {"CANCELLED"}
        mesh, armature = generated_objects(settings)
        if mesh is not None:
            delete_object_and_data(mesh)
        if armature is not None:
            delete_object_and_data(armature)
        restored = []
        role_objects = {}
        destination = context.scene.collection
        for backup in collection.objects:
            duplicate = backup.copy()
            duplicate.data = backup.data.copy()
            destination.objects.link(duplicate)
            duplicate.matrix_world = backup.matrix_world.copy()
            duplicate.hide_render = False
            duplicate.hide_set(False)
            if "uechaos_backup" in duplicate:
                del duplicate["uechaos_backup"]
            source_name = backup.get("uechaos_source_name")
            if source_name:
                duplicate.name = source_name
            role = backup.get("uechaos_role")
            if role:
                role_objects[role] = duplicate
            restored.append(duplicate)
        for role in ROLE_ORDER:
            setattr(settings, role, role_objects.get(role))
        settings.generated_mesh_name = ""
        settings.generated_armature_name = ""
        settings.last_status = trf("Restored objects: {count}", count=len(restored))
        if restored:
            set_active_only(context, restored[0])
        self.report({"INFO"}, settings.last_status)
        return {"FINISHED"}


class UECHAOS_OT_ExportFBX(Operator, ExportHelper):
    bl_idname = "uechaos.export_fbx"
    bl_label = "Export UE FBX"
    filename_ext = ".fbx"
    filter_glob: StringProperty(default="*.fbx", options={"HIDDEN"})

    def invoke(self, context, event):
        settings = context.scene.ue_chaos_vehicle_prep
        self.filepath = safe_name(settings.vehicle_name) + ".fbx"
        return ExportHelper.invoke(self, context, event)

    def execute(self, context):
        settings = context.scene.ue_chaos_vehicle_prep
        mesh, armature = generated_objects(settings)
        if mesh is None or armature is None:
            self.report({"ERROR"}, tr("Build the rig first"))
            return {"CANCELLED"}
        if not hasattr(bpy.ops.export_scene, "fbx"):
            self.report({"ERROR"}, tr("FBX exporter is not available in Blender"))
            return {"CANCELLED"}
        if context.object is not None and context.object.mode != "OBJECT":
            bpy.ops.object.mode_set(mode="OBJECT")
        previous_selected = list(context.selected_objects)
        previous_active = context.view_layer.objects.active
        bpy.ops.object.select_all(action="DESELECT")
        mesh.select_set(True)
        armature.select_set(True)
        context.view_layer.objects.active = armature
        desired = {
            "filepath": self.filepath,
            "use_selection": True,
            "object_types": {"ARMATURE", "MESH"},
            "global_scale": 1.0,
            "apply_unit_scale": True,
            "apply_scale_options": "FBX_SCALE_UNITS",
            "use_space_transform": True,
            "bake_space_transform": False,
            "axis_forward": "-Y",
            "axis_up": "Z",
            "use_mesh_modifiers": True,
            "mesh_smooth_type": "FACE",
            "use_tspace": True,
            "use_armature_deform_only": True,
            "add_leaf_bones": False,
            "primary_bone_axis": "X",
            "secondary_bone_axis": "-Z",
            "armature_nodetype": "NULL",
            "bake_anim": False,
            "path_mode": "COPY" if settings.copy_textures else "AUTO",
            "embed_textures": False,
            "use_custom_props": True,
        }
        available = {prop.identifier for prop in bpy.ops.export_scene.fbx.get_rna_type().properties}
        kwargs = {key: value for key, value in desired.items() if key in available}
        try:
            result = bpy.ops.export_scene.fbx(**kwargs)
            if "FINISHED" not in result:
                raise RuntimeError(tr("FBX exporter did not finish the export"))
            json_path = export_sidecar(self.filepath, settings, mesh, armature)
        except Exception as exc:
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}
        finally:
            bpy.ops.object.select_all(action="DESELECT")
            for obj in previous_selected:
                if obj.name in bpy.data.objects:
                    obj.select_set(True)
            if previous_active is not None and previous_active.name in bpy.data.objects:
                context.view_layer.objects.active = previous_active
        settings.last_status = trf("Exported: {fbx} | {json}", fbx=self.filepath, json=json_path)
        self.report({"INFO"}, tr("FBX and ChaosSetup JSON were exported"))
        return {"FINISHED"}


class UECHAOS_PT_ChaosVehicle(Panel):
    bl_label = "UE Chaos Vehicle Prep"
    bl_idname = "UECHAOS_PT_chaos_vehicle_prep"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "UE Chaos"

    def draw(self, context):
        settings = context.scene.ue_chaos_vehicle_prep
        layout = self.layout
        layout.prop(settings, "vehicle_name")
        scope = layout.box()
        scope.label(text=tr("Source"))
        scope.prop(settings, "scope", expand=True)
        if settings.scope == "COLLECTION":
            scope.prop(settings, "source_collection")
        buttons = layout.row(align=True)
        buttons.operator("uechaos.auto_detect", icon="VIEWZOOM")
        buttons.operator("uechaos.map_parts", icon="EYEDROPPER", text=tr("Manual"))
        required = layout.box()
        required.label(text=tr("Required Parts"))
        for role in REQUIRED_ROLES:
            required.prop(settings, role, text=role_label(role))
        optional = layout.box()
        optional.label(text=tr("Optional Moving Parts"))
        for role in OPTIONAL_ROLES:
            optional.prop(settings, role, text=role_label(role))
        setup = layout.box()
        setup.label(text=tr("Preparation"))
        setup.prop(settings, "include_unassigned")
        setup.prop(settings, "auto_align_forward")
        setup.prop(settings, "center_and_ground")
        setup.prop(settings, "apply_modifiers")
        setup.prop(settings, "apply_all_transforms")
        setup.prop(settings, "recalc_normals")
        if settings.recalc_normals:
            setup.prop(settings, "clear_custom_normals")
        setup.prop(settings, "merge_by_distance")
        if settings.merge_by_distance:
            setup.prop(settings, "merge_distance")
        rig = layout.box()
        rig.label(text=tr("Rig"))
        rig.prop(settings, "clear_vertex_groups")
        rig.prop(settings, "create_door_bones")
        if settings.create_door_bones:
            rig.prop(settings, "door_pivot_mode")
        rig.prop(settings, "create_steering_bone")
        rig.prop(settings, "create_backup")
        validation = layout.row(align=True)
        validation.operator("uechaos.validate", icon="CHECKMARK")
        validation.operator("uechaos.restore_backup", icon="LOOP_BACK")
        build = layout.column()
        build.scale_y = 1.4
        build.operator("uechaos.build_rig", icon="ARMATURE_DATA")
        export = layout.box()
        export.label(text=tr("Export"))
        export.prop(settings, "copy_textures")
        export.operator("uechaos.export_fbx", icon="EXPORT")
        if settings.last_status:
            status = layout.box()
            status.label(text=settings.last_status, icon="INFO")
        footer = layout.row()
        footer.alignment = "RIGHT"
        footer.label(text=tr("Author: Lexikonn"))


classes = (
    UECHAOS_PG_ChaosVehicleSettings,
    UECHAOS_OT_AutoDetect,
    UECHAOS_OT_MapParts,
    UECHAOS_OT_Validate,
    UECHAOS_OT_BuildRig,
    UECHAOS_OT_RestoreBackup,
    UECHAOS_OT_ExportFBX,
    UECHAOS_PT_ChaosVehicle,
)


def register():
    try:
        bpy.app.translations.unregister(__name__)
    except Exception:
        pass
    bpy.app.translations.register(__name__, TRANSLATIONS)
    for cls in classes:
        bpy.utils.register_class(cls)
    bpy.types.Scene.ue_chaos_vehicle_prep = PointerProperty(type=UECHAOS_PG_ChaosVehicleSettings)


def unregister():
    if hasattr(bpy.types.Scene, "ue_chaos_vehicle_prep"):
        del bpy.types.Scene.ue_chaos_vehicle_prep
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
    try:
        bpy.app.translations.unregister(__name__)
    except Exception:
        pass


if __name__ == "__main__":
    register()
