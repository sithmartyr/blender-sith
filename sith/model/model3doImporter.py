# Sith Blender Addon
# Copyright (c) 2019-2024 Crt Vavros

# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files (the "Software"), to deal
# in the Software without restriction, including without limitation the rights
# to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
# copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions:

# The above copyright notice and this permission notice shall be included in all
# copies or substantial portions of the Software.

# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
# OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
# SOFTWARE.

import bpy, bmesh, mathutils, os
from sith.types import BenchmarkMeter
from sith.utils import *
from typing import List, Union
from pathlib import Path

from . import model3doLoader
from .utils import *
from .model3do import (
    Model3do,
    Mesh3do
)

def import3do(file_path: Union[Path, str], mat_dirs: List[Union[Path, str]] = [], cmp_file: str = '', uvAbsolute_2_1: bool = True, importVertexColors: bool = True, importRadiusObj: bool = False, preserveOrder: bool = True, clearScene: bool = True) -> bpy.types.Object:
    with BenchmarkMeter(' done in {:.4f} sec.'):
        print("importing 3DO: %r..." % (file_path), end="")

        with BenchmarkMeter('Info: \nLoaded model from file in {:.4f} sec.', enabled=False):
            model, fileVersion = model3doLoader.load3do(file_path)
        isJkdf2 = (fileVersion == model3doLoader.Model3doFileVersion.Version2_1)
        if len(model.geosets) == 0:
            print("Info: Nothing to load because 3DO model doesn't contain any geoset.")
            return

        cmp = None
        if isJkdf2:
            # Load ColorMap
            try:
                cmp = getCmpFileOrDefault(cmp_file, file_path)
            except Exception as e:
                print(f"Warning: Failed to load ColorMap '{cmp_file}': {e}")
            if not cmp:
                print("Warning: Loading 3DO version 2.1 and no ColorMap was found!")

        if clearScene:
            clearAllScenes()

        # Load model's textures
        mat_dirs = [str(mat_dir) for mat_dir in mat_dirs]  # Convert to strings
        file_path_str = str(file_path)  # Convert file_path to string
        mat_dirs = _convert_to_absolute_paths(mat_dirs, os.path.dirname(file_path_str))  # convert relative paths to file_path base folder
        with BenchmarkMeter('Info: \nLoaded materials from files in {:.4f} sec.', enabled=False):
            importMaterials(model.materials, getDefaultMatFolders(file_path_str) + mat_dirs, cmp)

        # Create objects from model
        _create_objects_from_model(model, uvAbsolute=(isJkdf2 and uvAbsolute_2_1), geosetNum=0, vertexColors=importVertexColors, importRadiusObj=importRadiusObj, preserveOrder=preserveOrder)

        # Set model's insert offset and radius
        baseObj = bpy.data.objects.new(model.name, None)
        baseObj.empty_display_size = (0.0)
        bpy.context.collection.objects.link(baseObj)

        baseObj.location = model.insert_offset
        if importRadiusObj:
            _set_model_radius(baseObj, model.radius)

        firstChild = model.meshHierarchy[0].obj
        firstChild.parent_type = 'OBJECT'
        firstChild.parent = baseObj

        # Add model to the "Model3do" group
        if kGModel3do in bpy.data.collections:
            group = bpy.data.collections[kGModel3do]
        else:
            group = bpy.data.collections.new(kGModel3do)
            bpy.context.scene.collection.children.link(group)
        group.objects.link(baseObj)
        return baseObj


def _convert_to_absolute_paths(path_list: List[Union[Path, str]], cwd: Union[Path, str]) -> List[Union[Path, str]]:
    absolute_paths: List[Union[Path, str]] = []
    for path in path_list:
        if not os.path.isabs(path):
            # Convert to absolute path if it's relative
            absolute_path = os.path.abspath(Path(cwd) / path)
        else:
            # Keep the path as it is if it's already absolute
            absolute_path = path
        absolute_paths.append(absolute_path)
    return absolute_paths

def _set_obj_rotation(obj, rotation):
    objSetRotation(obj, rotation)

def _set_obj_pivot(obj, pivot):
    pvec = mathutils.Vector(pivot)
    if  obj.type == 'MESH' and obj.data is not None and pvec.length > 0:
        obj.data.transform(mathutils.Matrix.Translation(pvec))

def _make_radius_obj(name: str, parent, radius: float):
    if name in bpy.data.meshes:
        mesh = bpy.data.meshes[name]
    else:
        mesh = bpy.data.meshes.new(name)
        ro = bpy.data.objects.new(name , mesh)
        ro.display_type = 'WIRE'
        ro.hide_viewport = True
        ro.parent_type = 'OBJECT'
        ro.parent = parent
        bpy.context.scene.objects.link(ro)

    bm = bmesh.new()
    bmesh.ops.create_uvsphere(bm, u_segments=32, v_segments=16, diameter=radius)
    bm.to_mesh(mesh)
    bm.free()

def _set_model_radius(obj: bpy.types.Object, radius: float):
    _make_radius_obj(kModelRadius + obj.name, obj, radius)

def _set_mesh_radius(obj: bpy.types.Object, radius: float):
    _make_radius_obj(kMeshRadius + obj.name, obj, radius)

def _make_mesh(mesh3do: Mesh3do, uvAbsolute: bool, vertexColors: bool, mat_list: List):
    mesh = bpy.data.meshes.new(mesh3do.name)

    faces: List[List[int]] = []
    for face in mesh3do.faces:
        faces += [face.vertexIdxs]

    # Construct mesh
    mesh.from_pydata(mesh3do.vertices, [], faces)
    mesh.show_double_sided = True

    bm = bmesh.new()
    bm.from_mesh(mesh)
    bm.faces.ensure_lookup_table()

    vert_color = bm.loops.layers.color.verify()
    uv_layer   = bm.loops.layers.uv.verify()
    bm.faces.layers.tex.verify()
    bmMeshInit3doLayers(bm)

    # Set mesh materials and UV map
    for face in bm.faces:
        face3do = mesh3do.faces[face.index]

        # Set custom property for face type, geometry, light, texture mode
        bmFaceSetType(face, bm, face3do.type)
        bmFaceSetGeometryMode(face, bm, face3do.geometryMode)
        bmFaceSetLightMode(face, bm, face3do.lightMode)
        bmFaceSetTextureMode(face, bm, face3do.textureMode)
        bmFaceSetExtraLight(face, bm, face3do.color)

        # Set face normal
        face.normal = mesh3do.faces[face.index].normal

        # Set face material index
        mat = None
        if face3do.materialIdx > -1:
            mat_name = mat_list[face3do.materialIdx]
            mat = getGlobalMaterial(mat_name)
            if mat is None:
                print(f"\nWarning: Could not find or load material file '{mat_name}'")
                mat = makeNewGlobalMaterial(mat_name)

        if mat:
            if mat.name not in mesh.materials:
                mesh.materials.append(mat)
            face.material_index = mesh.materials.find(mat.name)

        # Set face texture
        img = None
        if mat and mat.node_tree.nodes:
            for node in mat.node_tree.nodes:
                if node.type == 'TEX_IMAGE':
                    img = node.image
                    break
            if img:
                tex_layer = bm.faces.layers.tex[uv_layer.name]
                face[tex_layer].image = img

        # Set vertices color and face uv map
        for idx, loop in enumerate(face.loops): # update vertices
            vidx             = loop.vert.index
            loop.vert.normal = mesh3do.normals[vidx]
            if vertexColors:
                loop[vert_color] = mesh3do.vertexColors[vidx]

            # Set UV coordinates
            luv    = loop[uv_layer]
            uvIdx  = face3do.uvIdxs[idx]
            if uvIdx < len(mesh3do.uvs):
                uv = mesh3do.uvs[uvIdx]
                if uvAbsolute: # Remove image size from uv
                    if img is not None:
                        uv = vectorDivide(mathutils.Vector(uv), mathutils.Vector(img.size))
                    elif face3do.materialIdx > -1:
                        print(f"\nWarning: Could not remove image size from UV coord due to missing image! mesh:'{mesh3do.name}' face:{face.index} uvIdx:{uvIdx}")
                luv.uv = (uv.x, -uv.y) # Note: Flipped v
            elif uvIdx > -1:
                print(f"Warning: UV index out of range {uvIdx} >= {len(mesh3do.uvs)}! mesh:'{mesh3do.name}' face:{face.index}")

    bm.to_mesh(mesh)
    bm.free()

    mesh.update()
    return mesh

def _create_objects_from_model(model, uvAbsolute, geosetNum, vertexColors, importRadiusObj, preserveOrder):
    # Ensure the objects are linked to the correct collection
    collection = bpy.context.collection

    for geoset in model.geosets:
        for mesh in geoset.meshes:
            meshName = mesh.name if not preserveOrder else f"{kNameOrderPrefix}{mesh.idx:02}_{mesh.name}"
            obj = bpy.data.objects.new(meshName, bpy.data.meshes.new(meshName))
            collection.objects.link(obj)

            bm = bmesh.new()
            for v in mesh.vertices:
                bm.verts.new(v)
            bm.verts.ensure_lookup_table()

            for f in mesh.faces:
                face = bm.faces.new([bm.verts[i] for i in f.vertexIdxs])
                face.material_index = f.materialIdx
                bmFaceSetType(face, bm, f.type)
                bmFaceSetGeometryMode(face, bm, f.geometryMode)
                bmFaceSetLightMode(face, bm, f.lightMode)
                bmFaceSetTextureMode(face, bm, f.textureMode)
                bmFaceSetExtraLight(face, bm, f.color)

            bm.to_mesh(obj.data)
            bm.free()

            obj.data.update()
            mesh.obj = obj

            # Set custom properties for object
            obj.sith_model3do_light_mode = mesh.lightMode.name
            obj.sith_model3do_texture_mode = mesh.textureMode.name
            obj.sith_model3do_hnode_idx = mesh.idx
            obj.sith_model3do_hnode_name = mesh.name
            obj.sith_model3do_hnode_flags = hex(mesh.flags)
            obj.sith_model3do_hnode_type = hex(mesh.type)

            if importRadiusObj:
                _set_mesh_radius(obj, mesh.radius)