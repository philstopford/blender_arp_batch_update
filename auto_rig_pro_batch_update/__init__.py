"""Batch update Auto-Rig Pro armatures in a list of .blend files."""

import json
import os

import bpy
from bpy.app.handlers import persistent


bl_info = {
    "name": "Auto-Rig Pro: Batch Rig Update",
    "author": "",
    "version": (1, 0, 0),
    "blender": (4, 2, 0),
    "location": "View3D > Sidebar > ARP Batch",
    "description": "Update Auto-Rig Pro rigs in a list of Blender scenes",
    "category": "Rigging",
}


_job = None


def _preferences():
    addon = bpy.context.preferences.addons.get(__package__)
    return addon.preferences if addon else None


def _is_arp_rig(obj):
    return (
        obj and obj.type == 'ARMATURE' and
        ('arp_rig_name' in obj or 'arp_rig_type' in obj or 'rig_type' in obj)
    )


def _needs_update(obj):
    if not _is_arp_rig(obj):
        return False
    data = obj.data
    if 'arp_updated' not in data:
        return True
    try:
        if int(str(data['arp_updated']).replace('.', '')) < 37814:
            return True
    except (TypeError, ValueError):
        return True
    if bpy.app.version >= (3, 0, 0) and 'arp_updated_3.0' not in data:
        return True
    return bpy.app.version >= (4, 0, 0) and 'arp_updated_4.0' not in data


def _breaking_changes(obj):
    data = obj.data
    result = []
    if 'c_jawbone.x' in data.bones and ('jawbone.x' not in data.bones or 'eyelid_top.l' not in data.bones):
        result.append('Eyelid and jawbone transforms can break existing poses or animations.')
    try:
        if int(str(data.get('arp_updated', '0')).replace('.', '')) < 36319:
            result.append('Optional feet and arms Z-up changes were available; the batch run left them disabled.')
    except (TypeError, ValueError):
        pass
    return result


def _read_paths(filepath):
    filepath = bpy.path.abspath(filepath)
    with open(filepath, 'r', encoding='utf-8') as stream:
        payload = json.load(stream)
    if isinstance(payload, dict):
        payload = payload.get('scenes', payload.get('files', []))
    if not isinstance(payload, list):
        raise ValueError('JSON must contain a list, or a scenes/files list')
    paths = []
    for item in payload:
        path = item.get('path') if isinstance(item, dict) else item
        if not isinstance(path, str) or not path.strip():
            raise ValueError('Each scene entry must be a file path or an object with a path')
        path = os.path.expanduser(path)
        if not os.path.isabs(path):
            path = os.path.join(os.path.dirname(os.path.abspath(filepath)), path)
        paths.append(os.path.abspath(path))
    return sorted(set(paths), key=str.casefold)


def _write_paths(filepath, paths):
    filepath = bpy.path.abspath(filepath)
    paths = sorted({os.path.abspath(path) for path in paths}, key=str.casefold)
    directory = os.path.dirname(os.path.abspath(filepath))
    if directory:
        os.makedirs(directory, exist_ok=True)
    with open(filepath, 'w', encoding='utf-8') as stream:
        json.dump({'scenes': paths}, stream, indent=2)
        stream.write('\n')


def _find_rigs():
    return [obj for obj in bpy.data.objects if _is_arp_rig(obj)]


def _process_current_file():
    global _job
    path = bpy.data.filepath
    rigs = _find_rigs()
    updated = []
    skipped = []
    warnings = []
    for rig in rigs:
        if not _needs_update(rig):
            skipped.append(rig.name)
            continue
        warnings.extend(f'{rig.name}: {message}' for message in _breaking_changes(rig))
        # Timers do not necessarily run with the same area/context as the
        # operator that started the batch.  Set the selection explicitly and
        # provide a window/view-layer override for Auto-Rig Pro.
        view_layer = bpy.context.view_layer
        view_layer.objects.active = rig
        for other in view_layer.objects:
            other.select_set(other == rig)
        window = bpy.context.window
        if window is not None:
            with bpy.context.temp_override(
                window=window, scene=bpy.context.scene, view_layer=view_layer,
                active_object=rig, object=rig,
            ):
                result = bpy.ops.arp.update_armature(required=True)
        else:
            result = bpy.ops.arp.update_armature(required=True)
        if 'FINISHED' in result:
            updated.append(rig.name)

    # Save every existing scene, including scenes that were already current.
    # This makes the batch result deterministic and fulfills the same output
    # contract for every entry in the input list.
    bpy.ops.wm.save_as_mainfile(filepath=path)
    _job['results'].append({
        'path': path, 'updated': updated, 'skipped': skipped,
        'warnings': warnings, 'error': '' if rigs else 'No Auto-Rig Pro rigs found',
    })


def _finish_job():
    global _job
    results = _job['results']
    lines = [f'Batch update complete: {len(results)} scene(s).']
    for item in results:
        if item['error']:
            lines.append(f"{item['path']}: {item['error']}")
        elif item['updated']:
            lines.append(f"{item['path']}: updated {', '.join(item['updated'])}")
        else:
            lines.append(f"{item['path']}: already up to date")
        lines.extend(f"WARNING: {warning}" for warning in item['warnings'])
    _job = None
    bpy.context.window_manager.popup_menu(
        lambda menu, context: [menu.layout.label(text=line[:240]) for line in lines],
        title='ARP Batch Rig Update', icon='INFO')


@persistent
def _load_post(_unused):
    if _job and _job['index'] < len(_job['paths']):
        bpy.app.timers.register(_continue_job, first_interval=0.2)


def _begin_job():
    if _job:
        # Load the file's UI so save_as_mainfile preserves that file's own
        # workspaces and editor layout.  The operator context is rebuilt in
        # _process_current_file after the load completes.
        bpy.ops.wm.open_mainfile(filepath=_job['paths'][0], load_ui=True)
    return None


def _continue_job():
    if not _job:
        return None
    path = _job['paths'][_job['index']]
    _job['index'] += 1
    if not os.path.isfile(path):
        _job['results'].append({'path': path, 'updated': [], 'skipped': [], 'warnings': [], 'error': 'File not found'})
    else:
        _process_current_file()
    if _job['index'] < len(_job['paths']):
        bpy.ops.wm.open_mainfile(
            filepath=_job['paths'][_job['index']], load_ui=True,
        )
    else:
        _finish_job()
    return None


class ARP_BATCH_Preferences(bpy.types.AddonPreferences):
    bl_idname = __package__
    json_path: bpy.props.StringProperty(
        name='Scene list JSON', subtype='FILE_PATH',
    )

    def draw(self, context):
        self.layout.prop(self, 'json_path')


class ARP_BATCH_OT_run(bpy.types.Operator):
    bl_idname = 'arp_batch.run'
    bl_label = 'Run Batch Update'

    def execute(self, context):
        global _job
        if _job:
            self.report({'ERROR'}, 'A batch update is already running')
            return {'CANCELLED'}
        preferences = _preferences()
        json_path = preferences.json_path if preferences else ''
        try:
            paths = _read_paths(json_path)
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            self.report({'ERROR'}, str(exc))
            return {'CANCELLED'}
        if not paths:
            self.report({'ERROR'}, 'The scene list is empty')
            return {'CANCELLED'}
        missing = [path for path in paths if not os.path.isfile(path)]
        if missing:
            self.report({'ERROR'}, f'Missing scene file: {missing[0]}')
            return {'CANCELLED'}
        _job = {
            'paths': paths,
            'json_path': bpy.path.abspath(json_path),
            'index': 0,
            'results': [],
        }
        bpy.app.timers.register(_begin_job, first_interval=0.1)
        return {'FINISHED'}


class ARP_BATCH_OT_load_json(bpy.types.Operator):
    bl_idname = 'arp_batch.load_json'
    bl_label = 'Load Scene List'

    def execute(self, context):
        preferences = _preferences()
        json_path = preferences.json_path if preferences else ''
        try:
            paths = _read_paths(json_path)
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            self.report({'ERROR'}, str(exc))
            return {'CANCELLED'}
        return {'FINISHED'}


class ARP_BATCH_OT_save_json(bpy.types.Operator):
    bl_idname = 'arp_batch.save_json'
    bl_label = 'Save Scene List'

    def execute(self, context):
        preferences = _preferences()
        json_path = preferences.json_path if preferences else ''
        if not json_path:
            self.report({'ERROR'}, 'Choose a JSON file first')
            return {'CANCELLED'}
        try:
            _read_paths(json_path)
        except OSError as exc:
            self.report({'ERROR'}, str(exc))
            return {'CANCELLED'}
        self.report({'INFO'}, 'Scene list saved')
        return {'FINISHED'}


class ARP_BATCH_OT_add_current(bpy.types.Operator):
    bl_idname = 'arp_batch.add_current'
    bl_label = 'Add Current Scene'

    def execute(self, context):
        path = bpy.data.filepath
        if not path:
            self.report({'ERROR'}, 'The current Blender file has not been saved')
            return {'CANCELLED'}
        preferences = _preferences()
        if not preferences or not preferences.json_path:
            self.report({'ERROR'}, 'Choose a JSON file first')
            return {'CANCELLED'}
        try:
            paths = _read_paths(preferences.json_path)
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            self.report({'ERROR'}, str(exc))
            return {'CANCELLED'}
        if path not in paths:
            paths.append(path)
            _write_paths(preferences.json_path, paths)
        return {'FINISHED'}


class ARP_BATCH_OT_add_file(bpy.types.Operator):
    bl_idname = 'arp_batch.add_file'
    bl_label = 'Add Scene File'

    filepath: bpy.props.StringProperty(subtype='FILE_PATH')
    directory: bpy.props.StringProperty(subtype='DIR_PATH')
    files: bpy.props.CollectionProperty(type=bpy.types.OperatorFileListElement)
    filter_glob: bpy.props.StringProperty(default='*.blend', options={'HIDDEN'})

    def invoke(self, context, event):
        context.window_manager.fileselect_add(self)
        return {'RUNNING_MODAL'}

    def execute(self, context):
        preferences = _preferences()
        if not preferences or not preferences.json_path:
            self.report({'ERROR'}, 'Choose a JSON file first')
            return {'CANCELLED'}
        selected = [item.name for item in self.files]
        if selected:
            paths_to_add = [os.path.abspath(os.path.join(self.directory, name)) for name in selected]
        else:
            paths_to_add = [os.path.abspath(self.filepath)]
        try:
            paths = _read_paths(preferences.json_path)
            for path in paths_to_add:
                if path.lower().endswith('.blend') and path not in paths:
                    paths.append(path)
            _write_paths(preferences.json_path, paths)
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            self.report({'ERROR'}, str(exc))
            return {'CANCELLED'}
        return {'FINISHED'}


class ARP_BATCH_OT_remove(bpy.types.Operator):
    bl_idname = 'arp_batch.remove'
    bl_label = 'Remove Selected Scene'

    index: bpy.props.IntProperty()

    def execute(self, context):
        preferences = _preferences()
        paths = _read_paths(preferences.json_path)
        if 0 <= self.index < len(paths):
            paths.pop(self.index)
            _write_paths(preferences.json_path, paths)
        return {'FINISHED'}


class ARP_BATCH_PT_panel(bpy.types.Panel):
    bl_label = 'ARP Batch Rig Update'
    bl_idname = 'ARP_BATCH_PT_panel'
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = 'ARP Batch'

    def draw(self, context):
        layout = self.layout
        preferences = _preferences()
        row = layout.row(align=True)
        if preferences:
            row.prop(preferences, 'json_path', text='JSON')
        row.operator(ARP_BATCH_OT_load_json.bl_idname, text='', icon='FILE_REFRESH')
        row.operator(ARP_BATCH_OT_save_json.bl_idname, text='', icon='FILE_TICK')

        if preferences and preferences.json_path:
            try:
                paths = _read_paths(preferences.json_path)
            except (OSError, ValueError, json.JSONDecodeError) as exc:
                layout.label(text=f'JSON error: {exc}')
                paths = []
            for index, path in enumerate(paths):
                row = layout.row(align=True)
                row.label(text=os.path.basename(path), icon='BLENDER')
                op = row.operator(ARP_BATCH_OT_remove.bl_idname, text='', icon='X')
                op.index = index
        else:
            layout.label(text='Choose a JSON file in the field above')
        row = layout.row(align=True)
        row.operator(ARP_BATCH_OT_add_current.bl_idname, icon='SCENE_DATA')
        row.operator(ARP_BATCH_OT_add_file.bl_idname, icon='FILE_FOLDER')
        layout.separator()
        layout.operator(ARP_BATCH_OT_run.bl_idname, icon='FILE_REFRESH')
        if _job:
            layout.label(text=f"Processing {_job['index']} / {len(_job['paths'])}")


classes = (
    ARP_BATCH_Preferences,
    ARP_BATCH_OT_run, ARP_BATCH_OT_load_json, ARP_BATCH_OT_save_json,
    ARP_BATCH_OT_add_current, ARP_BATCH_OT_add_file, ARP_BATCH_OT_remove,
    ARP_BATCH_PT_panel,
)


def register():
    for cls in classes:
        bpy.utils.register_class(cls)
    if _load_post not in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.append(_load_post)


def unregister():
    if _load_post in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.remove(_load_post)
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
