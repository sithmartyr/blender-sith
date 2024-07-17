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

import bpy, os
import numpy as np

from collections import namedtuple
from enum import IntEnum
from pathlib import Path
from struct import Struct
from typing import BinaryIO, List, NamedTuple, Optional, Union
from .cmp import ColorMap

file_magic        = b'MAT '
required_version  = 0x32
color_tex_width   = 32
color_tex_height  = 32
max_texture_slots = 18 # blender 2.79 limitation

class MatType(IntEnum):
    Color   = 0
    Texture = 2

class ColorMode(IntEnum):
    Indexed  = 0
    RGB      = 1
    RGBA     = 2

class ColorFormat(NamedTuple):
    color_mode: ColorMode
    bpp: int
    red_bpp: int
    green_bpp: int
    blue_bpp: int
    red_shl: int
    green_shl: int
    blue_shl: int
    red_shr: int
    green_shr: int
    blue_shr: int
    alpha_bpp: int
    alpha_shl: int
    alpha_shr: int

cf_serf = Struct('<14I')

class MatHeader(NamedTuple):
    magic: bytes
    version: int
    type: int
    record_count: int
    texture_count: int
    color_info: ColorFormat

mh_serf = Struct('<4siIii')

class MatColorRecord(NamedTuple):
    type: int
    color_index: int
    unknown_1: int
    unknown_2: int
    unknown_3: int
    unknown_4: int

mcr_serf = Struct('<6i')

class MatTextureRecord(NamedTuple):
    type: int
    color_index: int
    unknown_1: int
    unknown_2: int
    unknown_3: int
    unknown_4: int
    unknown_5: int
    unknown_6: int
    unknown_7: int
    cel_idx: int

mtr_serf = Struct('<10i')

class MatMipmapHeader(NamedTuple):
    width: int
    height: int
    transparent: int
    unknown_1: int
    transparent_color_num: int
    levels: int

mmm_serf = Struct('<6i')

class Pixel(NamedTuple):
    red: float
    green: float
    blue: float
    alpha: float

Pixels = List[Pixel]

class Mipmap(NamedTuple):
    width: int
    height: int
    color_info: ColorFormat
    pixel_data_array: Optional[List[Pixels]]

_linear_coef = 1.0 / 255.0

def _read_header(f: BinaryIO):
    rh  = bytearray(f.read(mh_serf.size))
    rcf = bytearray(f.read(cf_serf.size))
    cf  = ColorFormat._make(cf_serf.unpack(rcf))
    h   = MatHeader(*mh_serf.unpack(rh), cf)

    if h.magic != file_magic:
        raise ImportError("Invalid MAT file")
    if h.version != required_version:
        raise ImportError(f"Invalid MAT file version: {h.version}")
    if h.type != MatType.Color and h.type != MatType.Texture:
        raise ImportError(f"Invalid MAT file type: {h.type}")
    if h.type == MatType.Texture and h.record_count != h.texture_count:
        raise ImportError("MAT file record and texture count missmatch")
    if h.record_count <= 0:
        raise ImportError("MAT file contains no record(s)")
    if not (ColorMode.Indexed <= h.color_info.color_mode <= ColorMode.RGBA):
        raise ImportError(f"Invalid color mode: {h.color_info.color_mode}")
    if h.color_info.bpp % 8 != 0 and not (8 <= h.color_info.bpp <= 32):
        raise ImportError(f"Invalid color depth: {h.color_info.bpp}")
    return h

def _read_records(f: BinaryIO, h: MatHeader):
    records = []
    if h.type == MatType.Color:
        for _ in range(h.record_count):
            records.append(MatColorRecord._make(mcr_serf.unpack(f.read(mcr_serf.size))))
    else:
        for _ in range(h.record_count):
            records.append(MatTextureRecord._make(mtr_serf.unpack(f.read(mtr_serf.size))))
    return records

def _decode_indexed_pixel_data(pd, width: int, height: int, cmp: ColorMap, transparent_color: Optional[int] = None) -> Pixels:
    idx = np.frombuffer(pd, dtype=np.uint8) # image index buffer
    pal = np.insert(cmp.palette, 3, 255, axis=1) # expand palette to contain 255 for alpha
    if transparent_color is not None:
        pal[transparent_color][3] = 0

    # Convert indexed color to RGB
    raw_img = pal[idx]
    raw_img = raw_img.flatten().view(np.uint32)

    raw_img = np.flip(
        raw_img.view(np.uint8).reshape((height, width, 4)), axis=0
    ).flatten() * _linear_coef # get byte array and convert to linear
    return raw_img

def _get_pixel_data_size(width: int, height: int, bpp: int) -> int:
    return int(abs(width * height) * (bpp /8))

def _get_color_mask(bpc: int) -> int:
    return 0xFFFFFFFF >> (32 - bpc)

def _decode_rgba_pixel_data(pd, width: int, height: int, ci: ColorFormat) -> Pixels:
    type = np.uint8 if (ci.bpp == 8 or ci.bpp == 24) else np.uint16 if ci.bpp == 16 else np.uint32
    raw_img = np.frombuffer(pd, type)
    if ci.bpp == 24: # expand array to contain 255 for alpha
        raw_img = np.insert(raw_img.reshape((-1, 3)), 3, 255, axis=1) \
            .flatten().view(np.uint32)
    raw_img = raw_img.astype(np.uint32)

    def decode_alpha(img):
        a = 255
        if ci.alpha_bpp > 0:
            a = ((img >> ci.alpha_shl) & am) << ci.alpha_shr
            if ci.alpha_bpp == 1: # clamp rgb5551 to 0 or 255
                np.clip(a * 255, 0, 255, a) # faster than a[a>0] = 255
        return a

    # Decode image to 32 bit
    rm = _get_color_mask(ci.red_bpp)
    gm = _get_color_mask(ci.green_bpp)
    bm = _get_color_mask(ci.blue_bpp)
    am = _get_color_mask(ci.alpha_bpp)
    raw_img = ((raw_img >> ci.red_shl)   & rm)  << ci.red_shr         | \
              ((raw_img >> ci.green_shl) & gm)  << ci.green_shr << 8  | \
              ((raw_img >> ci.blue_shl)  & bm)  << ci.blue_shr  << 16 | \
              decode_alpha(raw_img) << 24

    # Flip image over Y-axis (height) and convert to linear
    raw_img = np.flip(
        raw_img.view(np.uint8).reshape((height, width, 4)), axis=[0]
    ).flatten() * _linear_coef # get byte array and convert to linear
    return raw_img

def _read_pixel_data(f: BinaryIO, width: int, height: int, ci: ColorFormat, cmp: Optional[ColorMap] = None, transparent_color: Optional[int] = None) -> Pixels:
    pd_size = _get_pixel_data_size(width, height, ci.bpp)
    pd = bytearray(f.read(pd_size))
    if ci.color_mode == ColorMode.Indexed or ci.bpp == 8:
        if not cmp:
            print("  Missing ColorMap, pixel data not decoded!")
            return []
        else:
            return _decode_indexed_pixel_data(pd, width, height, cmp, transparent_color)
    # RGB(A)
    return _decode_rgba_pixel_data(pd, width, height, ci)

def _read_mipmap(f: BinaryIO, cf: ColorFormat, cmp: Optional[ColorMap]):
    rh = mmm_serf.unpack(f.read(mmm_serf.size))
    h  = MatMipmapHeader(*rh)

    if cf.color_mode == ColorMode.Indexed:
        if cmp is None:
            raise ImportError("Missing ColorMap for indexed color mode")
        if len(cmp.palette) < 256:
            raise ImportError("ColorMap has less than 256 colors")

    mipmap = Mipmap(
        width=h.width,
        height=h.height,
        color_info=cf,
        pixel_data_array=None
    )

    if cf.color_mode == ColorMode.Indexed:
        pixel_data_array = []
        for _ in range(h.levels):
            pixel_data = np.frombuffer(f.read(h.width * h.height), dtype=np.uint8)
            rgba_data = [Pixel(
                red=cmp.palette[p].r * _linear_coef,
                green=cmp.palette[p].g * _linear_coef,
                blue=cmp.palette[p].b * _linear_coef,
                alpha=1.0
            ) for p in pixel_data]
            pixel_data_array.append(rgba_data)
        mipmap = mipmap._replace(pixel_data_array=pixel_data_array)

    return mipmap

def _get_tex_name(idx: int, mat_name: str) -> str:
    name = os.path.splitext(mat_name)[0]
    if idx > 0:
        name += '_cel_' + str(idx)
    return name

def _mat_add_new_texture(mat: bpy.types.Material, width: int, height: int, texIdx: int, pixdata: Optional[Pixels], hasTransparency: bool):
    img_name = _get_tex_name(texIdx, mat.name)
    if not img_name in bpy.data.images:
        img = bpy.data.images.new(
            img_name,
            width  = width,
            height = height
        )
    else:
        img = bpy.data.images[img_name]
        if img.has_data:
            img.scale(width, height)

    if pixdata is not None:
        flat_pixdata = [chan for pixel in pixdata for chan in (pixel.red, pixel.green, pixel.blue, pixel.alpha)]
        img.pixels = flat_pixdata
        #img.pack(as_png=True)
        img.update()
    else:
        img.generated_type   = 'UV_GRID'
        img.generated_width  = width
        img.generated_height = height

    tex                   = bpy.data.textures.new(img_name, 'IMAGE')
    tex.image             = img
    tex.use_preview_alpha = hasTransparency

    bsdf = mat.node_tree.nodes.get("Principled BSDF")
    if not bsdf:
        bsdf = mat.node_tree.nodes.new('ShaderNodeBsdfPrincipled')

    tex_image_node = mat.node_tree.nodes.new('ShaderNodeTexImage')
    tex_image_node.image = img

    tex_coord_node = mat.node_tree.nodes.new('ShaderNodeTexCoord')
    mapping_node = mat.node_tree.nodes.new('ShaderNodeMapping')

    mapping_node.vector_type = 'TEXTURE'
    mapping_node.inputs['Scale'].default_value[1] = -1 # Flippity flip (sad hack??)

    mat.node_tree.links.new(tex_coord_node.outputs['UV'], mapping_node.inputs['Vector'])
    mat.node_tree.links.new(mapping_node.outputs['Vector'], tex_image_node.inputs['Vector'])
    mat.node_tree.links.new(tex_image_node.outputs['Color'], bsdf.inputs['Base Color'])

    if hasTransparency:
        mat.blend_method = 'BLEND'
        mat.node_tree.links.new(tex_image_node.outputs['Alpha'], bsdf.inputs['Alpha'])

def _max_cels(len: int) -> int:
    return min(len, max_texture_slots)

def _make_color_textures(mat: bpy.types.Material, records, cmp: Optional[ColorMap]): # cmp is None then blank 64x64 textures is created
    # Creates 1 palette pixel color texture of size color_tex_height * color_tex_width
    for idx, r in zip(range(_max_cels(len(records))), records):
        pixmap: Optional[Pixels] = None
        if cmp:
            rgba   = (cmp.palette[r.color_index]) + (255,)
            pixmap = np.full((color_tex_height, color_tex_width, 4), rgba) \
                .flatten() * _linear_coef # flatten and convert to linear
        else:
            print("  Missing ColorMap, only texture size will be loaded!")

        # Make new texture from Pixels
        _mat_add_new_texture(mat, color_tex_width, color_tex_height, idx, pixmap, hasTransparency=False)

def importMat(filePath: Union[Path, str], cmp: Optional[ColorMap] = None) -> bpy.types.Material:
    f = open(filePath, 'rb')
    h = _read_header(f)
    records = _read_records(f, h)

    mat_name = os.path.basename(filePath)
    if mat_name in bpy.data.materials:
        mat = bpy.data.materials[mat_name]
        print(f"Info: MAT file '{mat_name}' already loaded, reloading textures!")
        for idx, s in enumerate(mat.texture_slots):
            if s is not None:
                if s.texture is not None:
                    bpy.data.textures.remove(s.texture)
                mat.texture_slots.clear(idx)
    else:
        mat = bpy.data.materials.new(mat_name)

    mat.use_nodes = True
    mat.node_tree.nodes.clear()

    bsdf = mat.node_tree.nodes.new('ShaderNodeBsdfPrincipled')
    output = mat.node_tree.nodes.new('ShaderNodeOutputMaterial')
    mat.node_tree.links.new(bsdf.outputs['BSDF'], output.inputs['Surface'])

    # Setting up the material to be shadeless
    if 'Specular' in bsdf.inputs:
        bsdf.inputs['Specular'].default_value = 0.0  # No specular highlight
    if 'Roughness' in bsdf.inputs:
        bsdf.inputs['Roughness'].default_value = 1.0  # Completely rough to diffuse light evenly

    if h.type == MatType.Color:
        _make_color_textures(mat, records, cmp)
    else:  # MAT contains textures
        use_transparency = True if h.color_info.alpha_bpp > 0 else False
        mat.blend_method = 'BLEND' if use_transparency else 'OPAQUE'
        mat.alpha_threshold = 0.0
        for i in range(0, _max_cels(h.texture_count)):
            print(f"Reading mipmap {i}")
            mm = _read_mipmap(f, h.color_info, cmp)
            _mat_add_new_texture(mat, mm.width, mm.height, i, mm.pixel_data_array[0] if mm.pixel_data_array else None, hasTransparency=use_transparency)
            print(f"Mipmap {i} read successfully")

    mat.node_tree.nodes['Principled BSDF'].inputs['Base Color'].default_value = (1, 1, 1, 1)
    return mat
