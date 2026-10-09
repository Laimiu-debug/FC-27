"""读取本机 RIFF EBXT 共享类型表，用其实际布局适配 FC27。

前代 SDK 仅提供已核对的 GUID/name-hash 名称信息；新版本偏移、大小、
类型签名及引用来自共享描述，不套用旧布局。未知字段保留哈希名称。
"""

from __future__ import annotations

import hashlib
import struct
import uuid

from fc27_assets import checked_range
from fc27_clr import Metadata, pe_metadata, serialized_string
from fc27_dbobject import FormatError, MAX_FILE_BYTES
from fc27_schema import NUMERIC_TYPES, Schemas, attribute


class SharedTypes:
    def __init__(self, data: bytes):
        if len(data) > MAX_FILE_BYTES or checked_range(data, 0, 4) != b"RIFF":
            raise FormatError("共享类型表不是支持大小的 RIFF")
        if struct.unpack_from("<I", data, 4)[0] + 8 != len(data) or data[8:12] != b"EBXT":
            raise FormatError("共享类型表的长度或 EBXT 标记无效")
        self.sha256 = hashlib.sha256(data).hexdigest()
        self.types, self.fields, self.identities = [], [], []
        self.mapping = {}
        position = 12
        chunks = 0
        while position < len(data):
            name, size = struct.unpack("<4sI", checked_range(data, position, 8))
            payload = checked_range(data, position + 8, size)
            checked_range(data, position + 8 + size, size & 1)
            position += 8 + size + (size & 1)
            if name not in (b"REFL", b"RFL2"):
                raise FormatError("共享类型表包含不支持的块")
            self._read(payload, name)
            chunks += 1
        if not chunks:
            raise FormatError("共享类型表没有描述块")

    def _read(self, data: bytes, tag: bytes):
        position = 0

        def take(size):
            nonlocal position
            raw = checked_range(data, position, size)
            position += size
            return raw

        def count():
            value = struct.unpack("<I", take(4))[0]
            if value > 500_000:
                raise FormatError("共享类型表元素数过多")
            return value

        first_type, first_field = len(self.types), len(self.fields)
        identities = []
        for _ in range(count()):
            raw = take(20)
            identities.append((str(uuid.UUID(bytes_le=raw[:16])), f"{struct.unpack_from('<I', raw, 16)[0]:08x}"))
        types = [struct.unpack("<Ii4H", take(16)) for _ in range(count())]
        fields = [struct.unpack("<II2H", take(12)) for _ in range(count())]
        if len(identities) != len(types):
            raise FormatError("共享类型身份数与描述数不一致")
        # 保留并检查未知尾表的边界；不赋予其未确认的语义。
        take(count() * 12)
        take(count() * 8)
        if tag == b"RFL2":
            if count() != 0:
                raise FormatError("RFL2 包含尚未支持的尾部扩展")
        if position != len(data):
            raise FormatError("共享类型表存在尾随字节")
        if len(set(identities)) != len(identities) or any(identity in self.mapping for identity in identities):
            raise FormatError("共享类型 GUID/签名重复，拒绝选择不唯一的布局")
        for i, identity in enumerate(identities):
            self.mapping[identity] = first_type + i
        for i, (name_hash, first, field_count, flags, size, alignment) in enumerate(types):
            if first < 0 or first + field_count > len(fields):
                raise FormatError("共享类型字段区间越界")
            if alignment not in (0, 1, 2, 4, 8, 16):
                raise FormatError("共享类型对齐不支持")
            self.types.append({"name_hash": name_hash, "field_index": first_field + first,
                               "field_count": field_count, "flags": flags, "size": size,
                               "alignment": alignment, "identity": identities[i]})
            self.identities.append(identities[i])
        for name_hash, offset, flags, reference in fields:
            if reference != 0xFFFF:
                if reference >= len(types):
                    raise FormatError("共享字段的类型引用越界")
                reference += first_type
            self.fields.append({"name_hash": name_hash, "offset": offset,
                                "flags": flags, "type_ref": reference})

    def adapt(self, sdk_data: bytes, guids: set[str]) -> Schemas:
        metadata = Metadata(pe_metadata(sdk_data))
        definitions = metadata.type_attributes(metadata.type_names_by_guids(guids))
        names = {}
        for definition in definitions:
            text, _ = serialized_string(attribute(definition["attrs"], "TypeGuidAttribute"), 2)
            guid = str(uuid.UUID(text))
            type_hash = struct.unpack_from("<I", attribute(definition["attrs"], "NameHashAttribute"), 2)[0]
            properties = {}
            for prop in definition["properties"].values():
                name_hash = struct.unpack_from("<I", attribute(prop["attrs"], "NameHashAttribute"), 2)[0]
                if name_hash in properties:
                    raise FormatError("SDK 字段名哈希重复，不能唯一适配")
                properties[name_hash] = prop
            if guid in names:
                raise FormatError("SDK 类型 GUID 无法唯一命名")
            names[guid] = {"name": definition["name"], "namespace": definition["namespace"],
                           "hash": type_hash, "properties": properties}
        result = object.__new__(Schemas)
        result.sdk_sha256 = hashlib.sha256(sdk_data).hexdigest()
        result.types = {}
        result.shared_sha256 = self.sha256
        result.version = "FC27"
        for i, descriptor in enumerate(self.types):
            guid, sig = descriptor["identity"]
            if guid not in names:
                continue
            naming = names[guid]
            if naming["hash"] != descriptor["name_hash"]:
                raise FormatError("SDK 的类型名称哈希与 FC27 共享描述不同")
            props = []
            first = descriptor["field_index"]
            for field in self.fields[first:first + descriptor["field_count"]]:
                flags = field["flags"]
                kind, category = (flags >> 5) & 31, (flags >> 1) & 15
                if kind == 0:
                    # 继承项不是本类的可修改玩法字段，原样留在载荷中。
                    continue
                old = naming["properties"].get(field["name_hash"])
                name = old["name"] if old else f"Field_{field['name_hash']:08x}"
                reference, element, element_identity = None, None, None
                if field["type_ref"] != 0xFFFF:
                    target = self.types[field["type_ref"]]
                    target_naming = names.get(target["identity"][0])
                    if target_naming:
                        if target_naming["hash"] != target["name_hash"]:
                            raise FormatError("引用类型名称哈希不匹配")
                        reference = target_naming["namespace"] + "." + target_naming["name"]
                    element_identity = {"guid": target["identity"][0], "signature": target["identity"][1]}
                elif kind == 3 and old:
                    # 仅以已核对名称的旧属性辅助识别；运行时目标仍需 EFIX 完整身份检查。
                    raw = attribute(old["attrs"], "FieldTypeInfoAttribute")
                    if (struct.unpack_from("<H", raw, 2)[0] >> 5) & 31 == 3:
                        reference, _ = serialized_string(raw, 8)
                if category == 4:
                    element = NUMERIC_TYPES[kind][1] if kind in NUMERIC_TYPES else reference
                    flags = (flags & ~(31 << 5)) | (4 << 5)
                width = (8 if category == 4 or kind == 3 else
                         struct.calcsize(NUMERIC_TYPES[kind][0]) if kind in NUMERIC_TYPES else 1)
                if field["offset"] + width > descriptor["size"]:
                    raise FormatError("FC27 字段位置超出共享描述的实例大小")
                props.append({"name": name, "name_hash": f"{field['name_hash']:08x}",
                              "flags": flags, "offset": field["offset"], "reference": reference,
                              "array_element": element, "array_identity": element_identity,
                              "descriptor_flags": field["flags"], "descriptor_type_ref": field["type_ref"],
                              "name_source": "SDK GUID + name hash" if old else "unresolved name hash"})
            result.types[guid, sig] = {"name": naming["name"], "namespace": naming["namespace"],
                                      "name_hash": f"{descriptor['name_hash']:08x}",
                                      "size": descriptor["size"], "alignment": descriptor["alignment"],
                                      "flags": descriptor["flags"], "properties": props,
                                      "layout_source": "FC27 SharedTypeDescriptors", "shared_index": i}
        return result
