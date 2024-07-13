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

import bpy, os.path
from pathlib import Path
from typing import Optional, Union, Tuple

from sith.material import ColorMap
from sith import bl_info

kMaxNameLen = 64
kDefaultCmp = 'dflt.cmp'

_fsys_case_sensitive = not Path(str(Path.home()).upper()).exists()

def isValidNameLen(name: str):
    return len(name) <= kMaxNameLen

def isASCII(s: str):
    return all(ord(c) < 128 for c in s)

def assertName(name: str):
    if not isValidNameLen(name):
        raise AssertionError(f"name error: len of '{name}' is greater then {kMaxNameLen} chars")

    if not isASCII(name):
        raise AssertionError(f"name error: '{name}' len does not contain all ASCII chars")

def findCmpFileInPath(cmpFile: Union[Path, str], path: Union[Path, str]) -> Optional[Path]:
    cmpFile = Path(cmpFile)
    modelDir: Path = Path(os.path.dirname(path))

    # try model folder
    path = modelDir / cmpFile
    if path.exists() and path.is_file():
        return path

    # try model folder / misc/cmp
    path = modelDir / Path('misc/cmp') / cmpFile
    if path.exists() and path.is_file():
        return path

    # try parent folder
    path = modelDir.parent / cmpFile
    if path.exists() and path.is_file():
        return path

    # try parent folder / misc/cmp
    path = modelDir.parent / Path('misc/cmp') / cmpFile
    if path.exists() and path.is_file():
        return path

    # try parent/parent folder / misc/cmp
    path = modelDir.parent.parent / Path('misc/cmp') / cmpFile
    if path.exists() and path.is_file():
        return path

    return None

def getCmpFileOrDefault(filepath: Union[Path, str], searchPath: Union[Path, str]) -> Optional[ColorMap]:
    cmp_file = Path(filepath)
    if len(filepath) == 0:
        cmp_file = Path(kDefaultCmp)
    if not cmp_file.is_file():
        cmp_file = findCmpFileInPath(cmp_file, searchPath)
    cmp = None
    if cmp_file is not None and cmp_file.is_file():
        cmp = ColorMap.load(cmp_file)
    return cmp

def getDefaultMatFolders(model3doPath: Union[Path, str]):
    path1 = os.path.dirname(model3doPath)
    path2 = os.path.join(path1, 'mat')
    path3 = os.path.abspath(os.path.join(path1, os.pardir))
    path3 = os.path.join(path3, 'mat')
    return [path1, path2, path3]

def getFilePathInDir(filename: str, dirPath: Union[Path, str], insensitive: bool = True):
    "Returns string file path in dir if file exists otherwise None"

    if not os.path.isdir(dirPath) or len(filename) < 1:
        return None

    def file_exists(filePath: str):
        return os.path.isfile(filePath) and os.access(filePath, os.R_OK)

    filePath = os.path.join(dirPath, filename)
    if file_exists(filePath):
        return filePath

    if _fsys_case_sensitive and insensitive:
        # Try to find the file by lower-cased name
        filename = filename.lower()
        filePath = os.path.join(dirPath, filename)
        if file_exists(filePath):
            return filePath

        # Ok, now let's go through all files in folder and
        # try to find file by case insensitive comparing it.
        # to other file names.
        for f in os.listdir(dirPath):
            filePath = os.path.join(dirPath, f)
            if file_exists(filePath) and f.lower() == filename:
                return filePath

def getGlobalMaterial(name: str) -> Optional[bpy.types.Material]:
    if name in bpy.data.materials:
        return bpy.data.materials[name]

    name = name.lower()
    if name in bpy.data.materials:
        return bpy.data.materials[name]

    for mat in bpy.data.materials:
        if mat.name.lower() == name:
            return mat

def makeNewGlobalMaterial(mat_name: str) -> bpy.types.Material:
    mat = bpy.data.materials.new(name=mat_name)
    mat.use_nodes = True
    bsdf = mat.node_tree.nodes["Principled BSDF"]
    tex_image = mat.node_tree.nodes.new('ShaderNodeTexImage')
    tex_image.image = bpy.data.images.new(name=mat_name, width=1024, height=1024)  # Placeholder image, adjust as necessary
    mat.node_tree.links.new(bsdf.inputs['Base Color'], tex_image.outputs['Color'])
    mat.use_backface_culling = False  # Ensure double-sided rendering
    return mat

def clearSceneAnimData(scene):
    scene.timeline_markers.clear()
    for ob in scene.objects:
        ob.animation_data_clear()

def clearAllScenes():
    # Ensure all objects are unlinked from their collections
    for scene in bpy.data.scenes:
        for obj in scene.objects:
            if obj.name in scene.collection.objects:
                scene.collection.objects.unlink(obj)
        for collection in scene.collection.children:
            for obj in collection.objects:
                collection.objects.unlink(obj)
            scene.collection.children.unlink(collection)
    
    # Remove all objects
    bpy.ops.object.select_all(action='SELECT')
    bpy.ops.object.delete(use_global=False)

    # Remove all meshes
    bpy.ops.outliner.orphans_purge(do_local_ids=True, do_linked_ids=True, do_recursive=True)

    # Remove all materials
    for material in bpy.data.materials:
        bpy.data.materials.remove(material)
    
    # Remove all collections
    for collection in bpy.data.collections:
        bpy.data.collections.remove(collection)

def getExportFileHeader(prefix: str) -> str:
    version: Tuple[int] = bl_info['version']
    verstr = '.'.join([str(v) for v in version])
    if 'pre_release' in bl_info:
        verstr += '-' + bl_info['pre_release']
    return f"{prefix} created with Blender Sith addon v{verstr} by {bl_info['author']}"
