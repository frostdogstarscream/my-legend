"""Bounded Spine 2.1 binary-to-JSON reader for local asset inspection.

Input is the documented 2.1 runtime binary layout (not a .spine editor file).
Keeps animation values and atlas coordinates unchanged. No image generation or
conversion to a newer Spine format is performed. Rejects unknown/trailing data.
"""
import math
import struct


class SpineFormatError(ValueError):
    pass


class Reader:
    def __init__(self, data):
        self.data, self.pos = data, 0

    def take(self, n):
        if n < 0 or self.pos + n > len(self.data):
            raise SpineFormatError(f'truncated binary at {self.pos}')
        value = self.data[self.pos:self.pos+n]
        self.pos += n
        return value

    def byte(self):
        return self.take(1)[0]

    def boolean(self):
        value = self.byte()
        if value not in (0, 1):
            raise SpineFormatError(f'invalid boolean at {self.pos-1}')
        return bool(value)

    def integer(self, zigzag=False):
        value = 0
        for shift in range(0, 35, 7):
            b = self.byte()
            value |= (b & 127) << shift
            if not b & 128:
                return (value >> 1) ^ -(value & 1) if zigzag else value
        raise SpineFormatError('invalid varint')

    def count(self):
        n = self.integer()
        if n > 1_000_000:
            raise SpineFormatError('array exceeds local import budget')
        return n

    def string(self):
        n = self.count()
        return None if n == 0 else self.take(n-1).decode('utf-8')

    def number(self):
        value = struct.unpack('>f', self.take(4))[0]
        if not math.isfinite(value):
            raise SpineFormatError('nonfinite float')
        return value

    def color(self):
        return self.take(4).hex()

    def floats(self):
        return [self.number() for _ in range(self.count())]

    def curve(self, frame):
        kind = self.byte()
        if kind == 1:
            frame['curve'] = 'stepped'
        elif kind == 2:
            frame['curve'] = [self.number() for _ in range(4)]
        elif kind != 0:
            raise SpineFormatError('unknown curve')


def decode(data):
    r = Reader(data)
    h, version = r.string(), r.string()
    if version not in ('2.1.25', '2.1.26', '2.1.27'):
        raise SpineFormatError(f'unsupported version: {version}')
    result = {'skeleton': {'hash': h, 'spine': version, 'width': r.number(), 'height': r.number()},
              'bones': [], 'ik': [], 'slots': [], 'skins': {}, 'events': {}, 'animations': {}}
    extra = r.boolean()
    if extra:
        result['skeleton']['images'] = r.string()
    bones, slots, ik, skins = (result[k] for k in ('bones', 'slots', 'ik', 'skins'))
    for _ in range(r.count()):
        bone = {'name': r.string()}
        parent = r.integer() - 1
        if parent >= 0:
            bone['parent'] = bones[parent]['name']
        bone.update({k: r.number() for k in ('x', 'y', 'scaleX', 'scaleY', 'rotation', 'length')})
        bone.update({k: r.boolean() for k in ('flipX', 'flipY', 'inheritScale', 'inheritRotation')})
        if extra:
            bone['color'] = r.color()
        bones.append(bone)
    for _ in range(r.count()):
        ik.append({'name': r.string(), 'bones': [bones[r.integer()]['name'] for _ in range(r.count())],
                   'target': bones[r.integer()]['name'], 'mix': r.number(), 'bendPositive': r.byte() == 1})
    for _ in range(r.count()):
        slots.append({'name': r.string(), 'bone': bones[r.integer()]['name'], 'color': r.color(),
                      'attachment': r.string(), 'additive': 'true' if r.boolean() else 'false'})

    def attachment(key):
        a = {'name': r.string() or key}
        kind = r.byte()
        types = ['region', 'boundingbox', 'mesh', 'skinnedmesh']
        if kind >= len(types):
            raise SpineFormatError(f'unknown attachment {kind}')
        a['type'] = types[kind]
        if kind == 1:
            a['vertices'] = r.floats()
            return a
        a['path'] = r.string() or a['name']
        if kind == 0:
            a.update({k: r.number() for k in ('x', 'y', 'scaleX', 'scaleY', 'rotation', 'width', 'height')})
            a['color'] = r.color()
            return a
        a['uvs'] = r.floats()
        a['triangles'] = [struct.unpack('>H', r.take(2))[0] for _ in range(r.count())]
        a['vertices'] = r.floats()
        a['color'], a['hull'] = r.color(), r.integer()
        if extra:
            a['edges'] = [r.integer() for _ in range(r.count())]
            a.update(width=r.number(), height=r.number())
        return a

    def skin(name):
        slot_count = r.count()
        if not slot_count:
            return
        target = skins[name] = {}
        for _ in range(slot_count):
            slot = target[slots[r.integer()]['name']] = {}
            for _ in range(r.count()):
                key = r.string()
                slot[key] = attachment(key)

    skin('default')
    for _ in range(r.count()):
        skin(r.string())
    for _ in range(r.count()):
        name = r.string()
        result['events'][name] = {'int': r.integer(True), 'float': r.number(), 'string': r.string()}

    def frames(kind, n):
        out = []
        for i in range(n):
            f = {'time': r.number()}
            if kind == 'attachment':
                f['name'] = r.string()
            elif kind == 'color':
                f['color'] = r.color()
            elif kind == 'rotate':
                f['angle'] = r.number()
            elif kind in ('translate', 'scale'):
                f.update(x=r.number(), y=r.number())
            elif kind in ('flipX', 'flipY'):
                f['x' if kind == 'flipX' else 'y'] = r.boolean()
            elif kind == 'ik':
                f.update(mix=r.number(), bendPositive=r.byte() == 1)
            elif kind == 'ffd':
                size = r.count()
                if size:
                    f['offset'] = r.integer()
                    f['vertices'] = [r.number() for _ in range(size)]
            if i < n-1 and kind not in ('attachment', 'flipX', 'flipY'):
                r.curve(f)
            out.append(f)
        return out

    for _ in range(r.count()):
        name = r.string()
        anim = result['animations'][name] = {'slots': {}, 'bones': {}, 'ik': {}, 'ffd': {}, 'draworder': [], 'events': []}
        for category, items in (('slots', slots), ('bones', bones)):
            for _ in range(r.count()):
                target = anim[category][items[r.integer()]['name']] = {}
                for _ in range(r.count()):
                    kind = r.byte()
                    names = {0: 'scale', 1: 'rotate', 2: 'translate', 3: 'attachment', 4: 'color', 5: 'flipX', 6: 'flipY'}
                    if kind not in names or (category == 'slots') != (kind in (3, 4)):
                        raise SpineFormatError(f'unsupported {category} timeline {kind}')
                    target[names[kind]] = frames(names[kind], r.count())
        for _ in range(r.count()):
            key = ik[r.integer()]['name']
            anim['ik'][key] = frames('ik', r.count())
        for _ in range(r.count()):
            sk = anim['ffd'][list(skins)[r.integer()]] = {}
            for _ in range(r.count()):
                slot = sk[slots[r.integer()]['name']] = {}
                for _ in range(r.count()):
                    key = r.string()
                    slot[key] = frames('ffd', r.count())
        for _ in range(r.count()):
            offsets = []
            for _ in range(r.count()):
                key, off = slots[r.integer()]['name'], r.integer()
                offsets.append({'slot': key, 'offset': off if off < 2**31 else off-2**32})
            anim['draworder'].append({'offsets': offsets, 'time': r.number()})
        event_names = list(result['events'])
        for _ in range(r.count()):
            f = {'time': r.number(), 'name': event_names[r.integer()], 'int': r.integer(True), 'float': r.number()}
            f['string'] = r.string() if r.boolean() else result['events'][f['name']]['string']
            anim['events'].append(f)
    if r.pos != len(data):
        raise SpineFormatError(f'trailing data: {len(data)-r.pos} bytes')
    return result


def animation_duration(value):
    if isinstance(value, dict):
        return max([float(value.get('time', 0))] + [animation_duration(v) for v in value.values()])
    if isinstance(value, list):
        return max([0.0] + [animation_duration(v) for v in value])
    return 0.0
