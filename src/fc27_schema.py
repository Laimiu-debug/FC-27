"""按完整 GUID/签名复用静态 SDK 描述，解析受支持的数组和 FloatCurve。"""

from __future__ import annotations

import hashlib
import struct
import uuid

from fc27_assets import checked_range
from fc27_clr import Metadata, pe_metadata, serialized_string
from fc27_dbobject import FormatError
from fc27_riff import verify_ebx


NUMERIC_TYPES = {8: ("<i", None), 10: ("<?", "System.Boolean"),
                 11: ("<b", "System.SByte"), 12: ("<B", "System.Byte"),
                 13: ("<h", "System.Int16"), 14: ("<H", "System.UInt16"),
                 15: ("<i", "System.Int32"), 16: ("<I", "System.UInt32"),
                 17: ("<q", "System.Int64"), 18: ("<Q", "System.UInt64"),
                 19: ("<f", "System.Single"), 20: ("<d", "System.Double")}


def attribute(attributes: list[dict], name: str) -> bytes:
    found = [a for a in attributes if a["name"] == name]
    if len(found) != 1:
        raise FormatError(f"SDK 缺少唯一属性：{name}")
    raw = bytes.fromhex(found[0]["blob"])
    if checked_range(raw, 0, 2) != b"\x01\0":
        raise FormatError("SDK 属性序言无效")
    return raw


def signature(attributes: list[dict]) -> str:
    raw = attribute(attributes, "TypeSignatureAttribute")
    if len(raw) != 8 or raw[-2:] != b"\0\0":
        raise FormatError("SDK 签名属性格式不支持")
    return f"{struct.unpack_from('<I', raw, 2)[0]:08x}"


class Schemas:
    def __init__(self, definitions: list[dict], sdk_sha256: str):
        self.sdk_sha256 = sdk_sha256
        self.types = {}
        for definition in definitions:
            attrs = definition["attrs"]
            raw = attribute(attrs, "TypeGuidAttribute")
            text, end = serialized_string(raw, 2)
            if text is None or raw[end:] != b"\0\0":
                raise FormatError("SDK GUID 属性格式不支持")
            guid = str(uuid.UUID(text))
            raw = attribute(attrs, "TypeInfoAttribute")
            if len(raw) != 9 or raw[-2:] != b"\0\0":
                raise FormatError("SDK TypeInfo 属性格式不支持")
            flags, alignment, size = struct.unpack_from("<HBH", raw, 2)
            if not size or alignment not in (1, 2, 4, 8, 16):
                raise FormatError("SDK 类型大小或对齐无效")
            props = []
            for property in definition["properties"].values():
                raw = attribute(property["attrs"], "FieldTypeInfoAttribute")
                field_flags, offset = struct.unpack("<HI", checked_range(raw, 2, 6))
                reference, end = serialized_string(raw, 8)
                kind = (field_flags >> 5) & 31
                width = (struct.calcsize(NUMERIC_TYPES[kind][0]) if kind in NUMERIC_TYPES
                         else {3: 8, 4: 8}.get(kind, 1))
                if raw[end:] != b"\0\0" or offset + width > size:
                    raise FormatError("SDK 字段属性或位置无效")
                props.append({"name": property["name"], "flags": field_flags,
                              "offset": offset, "reference": reference,
                              "array_element": property.get("array_element"),
                              "name_hash": (f"{struct.unpack_from('<I', attribute(property['attrs'], 'NameHashAttribute'), 2)[0]:08x}"
                                            if any(a['name'] == 'NameHashAttribute' for a in property['attrs']) else None)})
            key = guid, signature(attrs)
            if key in self.types:
                raise FormatError("SDK 类型 GUID/签名重复")
            self.types[key] = {"name": definition["name"], "namespace": definition.get("namespace", ""), "size": size,
                               "alignment": alignment, "flags": flags, "properties": props,
                               "name_hash": (f"{struct.unpack_from('<I', attribute(attrs, 'NameHashAttribute'), 2)[0]:08x}"
                                             if any(a['name'] == 'NameHashAttribute' for a in attrs) else None)}

    @classmethod
    def from_sdk(cls, data: bytes, guids: set[str]) -> Schemas:
        metadata = Metadata(pe_metadata(data))
        names = metadata.type_names_by_guids(guids)
        return cls(metadata.type_attributes(names), hashlib.sha256(data).hexdigest())


def named_values(data: bytes, schemas: Schemas) -> dict:
    info = verify_ebx(data)
    start = info["payload_file_offset"]
    payload = checked_range(data, start, info["payload_size"])
    types = info["types"]
    instances = set(info["instance_offsets"])
    pointer_offsets = set(info["pointer_offsets"])
    descriptors = {}
    for descriptor in info["array_descriptors"]:
        offset = descriptor["offset"]
        if offset in descriptors and descriptors[offset] != descriptor:
            raise FormatError("同一数组位置有不同描述")
        descriptors[offset] = descriptor

    def resolve(index: int) -> dict:
        if not 0 <= index < len(types):
            raise FormatError("EBX 类型索引越界")
        identity = types[index]
        try:
            return schemas.types[identity["guid"], identity["signature"]]
        except KeyError as exc:
            raise FormatError("SDK 与 EBX 的完整类型 GUID/签名不匹配") from exc

    def object_type(offset: int) -> dict:
        if offset not in instances:
            raise FormatError("字段指针没有指向 EFIX 实例")
        schema = resolve(struct.unpack("<H", checked_range(payload, offset, 2))[0])
        checked_range(payload, offset, schema["size"])
        if offset % schema["alignment"]:
            raise FormatError("SDK 类型对齐与实例不匹配")
        return schema

    def array_element_type(full_name: str | None) -> dict:
        if full_name is None:
            raise FormatError("SDK 缺少结构数组的 CLR 集合元素类型")
        matches = []
        for i, identity in enumerate(types):
            schema = schemas.types.get((identity["guid"], identity["signature"]))
            if schema is not None:
                name = schema["namespace"] + "." + schema["name"]
                if name == full_name:
                    matches.append(i)
        if len(matches) != 1:
            raise FormatError("结构数组元素的完整名称和 GUID/签名不能唯一对应")
        return resolve(matches[0])

    leaves, shapes, warnings = {}, {}, []

    def leaf(path: str, offset: int, fmt="<f"):
        raw = checked_range(payload, offset, struct.calcsize(fmt))
        leaves[path] = {"offset": start + offset, "raw_hex": raw.hex(),
                        "value": struct.unpack(fmt, raw)[0], "format": fmt}

    def array(position: int) -> dict:
        if position not in pointer_offsets:
            raise FormatError("字段的数组指针不在 EFIX 中")
        relative = struct.unpack("<i", checked_range(payload, position, 4))[0]
        if checked_range(payload, position + 4, 4) != bytes(4):
            raise FormatError("磁盘数组指针的高 32 位不为零")
        values_offset = position + relative
        # RIFF writer 为所有空数组共享 ArrayOffset + 16 的零值位置；没有 EBXX 项。
        # 只接受 EFIX 明确指向的 32 字节全零预留区，不能将任意 count=0 当成数组。
        if values_offset == info["arrays_offset"] + 16 and values_offset not in descriptors:
            if checked_range(payload, info["arrays_offset"], 32) != bytes(32):
                raise FormatError("共享空数组区不是 32 字节全零")
            return {"offset": values_offset, "count": 0, "identifier": None,
                    "flags": None, "type_ref": None, "shared_empty": True}
        try:
            descriptor = descriptors[values_offset]
        except KeyError as exc:
            raise FormatError("字段的数组指针与 EBXX 不对应") from exc
        count = struct.unpack("<I", checked_range(payload, values_offset - 4, 4))[0]
        if count != descriptor["count"]:
            raise FormatError("字段数组元素数与 EBXX 不符")
        return descriptor

    def float_curve(path: str, position: int):
        relative = struct.unpack("<q", checked_range(payload, position, 8))[0]
        if relative == 0:
            shapes[path] = {"kind": "null"}
            return
        if position not in pointer_offsets or relative & 1:
            raise FormatError("仅支持内部 FloatCurve 指针")
        curve_position = position + relative
        schema = object_type(curve_position)
        if schema["name"] != "FloatCurve":
            raise FormatError("字段没有指向 FloatCurve 类型")
        properties = {p["name"]: p for p in schema["properties"]}
        if not all(p in properties for p in ("Points", "MinX", "MaxX")):
            raise FormatError("FloatCurve 字段布局不完整")
        if (properties["Points"]["flags"] >> 5) & 31 != 4 or any(
                (properties[n]["flags"] >> 5) & 31 != 19 for n in ("MinX", "MaxX")):
            raise FormatError("FloatCurve 字段类型不匹配")
        descriptor = array(curve_position + properties["Points"]["offset"])
        if descriptor.get("shared_empty"):
            shapes[path] = {"kind": "float_curve", "count": 0, "point_size": None,
                            "array_identifier": None}
            for name in ("MinX", "MaxX"):
                leaf(path + "." + name, curve_position + properties[name]["offset"])
            return
        flags = descriptor["flags"]
        if descriptor["count"] == 0 and descriptor["type_ref"] == 0xFFFF:
            # 前代 writer 的空结构数组可保留 Array 类型标记而没有 Point 类型引用。
            if (flags >> 1) & 15 != 4 or (flags >> 5) & 31 not in (2, 4):
                raise FormatError("空 FloatCurve Points 的数组标记不支持")
            shapes[path] = {"kind": "float_curve", "count": 0, "point_size": None,
                            "array_identifier": descriptor["identifier"]}
            for name in ("MinX", "MaxX"):
                leaf(path + "." + name, curve_position + properties[name]["offset"])
            return
        # EBXX 的 type_ref 在前代样本中可超出 EFIX 类型表，不将其猜作本地索引。
        # 使用 CLR 集合元素类型，再与 EFIX 的完整 GUID/签名交叉核对。
        point = array_element_type(properties["Points"]["array_element"])
        if point["name"] != "FloatCurvePoint":
            raise FormatError("FloatCurve Points 类型不匹配")
        if (flags >> 5) & 31 != 2 or (flags >> 1) & 15 != 4:
            # 仅经完整包 SHA256 验证的 Anth 样本采用此研究读取策略。
            # 其 8 个资产的 EBXX 标记不合法，实际布局来自完整身份匹配的 SDK/CLR。
            # 保留异常警告；此策略不修复标记，也不开放给未知包或 FC27 候选。
            if not (getattr(schemas, "allow_legacy_curve_metadata", False) and flags == 0x4020
                    and descriptor["type_ref"] == 2 and descriptor["identifier"] == "19036493"
                    and point["size"] == 28):
                raise FormatError("FloatCurve Points 不是结构数组")
            warnings.append({"field": path, "reason": "known reference EBXX flags conflict with verified CLR element type",
                             "flags": flags, "type_ref": descriptor["type_ref"], "metadata_repaired": False})
        offset, count = descriptor["offset"], descriptor["count"]
        checked_range(payload, offset, count * point["size"])
        shapes[path] = {"kind": "float_curve", "count": count,
                        "point_size": point["size"], "array_identifier": descriptor["identifier"]}
        for name in ("MinX", "MaxX"):
            leaf(path + "." + name, curve_position + properties[name]["offset"])
        for i in range(count):
            for p in point["properties"]:
                kind = (p["flags"] >> 5) & 31
                if kind not in (8, 19):
                    raise FormatError("FloatCurvePoint 出现未知字段类型")
                leaf(f"{path}.Points[{i}].{p['name']}", offset + i*point["size"] + p["offset"],
                     "<i" if kind == 8 else "<f")

    if not info["instance_offsets"]:
        raise FormatError("EBX 没有根实例")
    root = info["instance_offsets"][0]
    schema = object_type(root)
    unsupported = []
    for prop in schema["properties"]:
        position = root + prop["offset"]
        kind = (prop["flags"] >> 5) & 31
        name = prop["name"]
        if kind == 4:
            descriptor = array(position)
            if descriptor.get("shared_empty"):
                shapes[name] = {"kind": "empty_array", "count": 0,
                                "array_identifier": None, "element_type": prop["array_element"]}
                continue
            flags = descriptor["flags"]
            element_kind = (flags >> 5) & 31
            if element_kind == 8 and descriptor["type_ref"] != 0xFFFF:
                # 枚举引用指向共享描述表；当前无法按 EFIX 索引解释，保留为未支持。
                unsupported.append(name)
                continue
            if element_kind in NUMERIC_TYPES and (flags >> 1) & 15 == 4:
                fmt, expected_element = NUMERIC_TYPES[element_kind]
                if expected_element is not None and prop["array_element"] != expected_element:
                    raise FormatError("数组的 CLR 元素类型与 EBXX 不匹配")
                if descriptor["type_ref"] != 0xFFFF:
                    raise FormatError("数值数组出现了未支持的类型引用")
                count, offset = descriptor["count"], descriptor["offset"]
                width = struct.calcsize(fmt)
                checked_range(payload, offset, width * count)
                shapes[name] = {"kind": "float_array" if element_kind == 19 else "numeric_array",
                                "count": count, "format": fmt,
                                "array_identifier": descriptor["identifier"]}
                for i in range(count):
                    leaf(f"{name}[{i}]", offset + width*i, fmt)
            else:
                unsupported.append(name)
        elif kind == 3:
            if prop["reference"] == "Frostbite.Core.FloatCurve":
                float_curve(name, position)
            elif hasattr(schemas, "shared_sha256"):
                relative = struct.unpack("<q", checked_range(payload, position, 8))[0]
                if relative and not relative & 1 and position in pointer_offsets:
                    target = object_type(position + relative)
                    if target["name"] == "FloatCurve" and target["namespace"] == "Frostbite.Core":
                        float_curve(name, position)
                        continue
                unsupported.append(name)
            else:
                unsupported.append(name)
        elif kind in NUMERIC_TYPES:
            fmt = NUMERIC_TYPES[kind][0]
            shapes[name] = {"kind": "float32" if kind == 19 else "numeric", "format": fmt}
            leaf(name, position, fmt)
        else:
            unsupported.append(name)
    return {"root_type": schema["name"], "root_identity": types[struct.unpack_from('<H', payload, root)[0]],
            "root_name_hash": schema.get("name_hash"),
            "sdk_sha256": schemas.sdk_sha256,
            "mapping_basis": ("FC27 SharedTypeDescriptors GUID/signature/offset + EFIX/EBXX"
                              if hasattr(schemas, "shared_sha256") else "SDK GUID/signature/offset + EFIX/EBXX"),
            "shared_types_sha256": getattr(schemas, "shared_sha256", None),
            "sdk_version": getattr(schemas, "version", "FC26"), "game_semantics_verified": False,
            "field_name_hashes": {p['name']: p.get('name_hash') for p in schema['properties']},
            "unresolved_names": [p['name'] for p in schema['properties'] if p.get('name_source') == 'unresolved name hash'],
            "metadata_warnings": warnings,
            "shapes": shapes, "leaves": leaves, "unsupported_fields": unsupported}
