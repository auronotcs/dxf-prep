"""Optional native-job cache: bounded compressed JSON, never disk pickles.

Only cleanout/rest motion and region calculations persist. No validation or
collision approvals persist. Exact runtime input keys still gate every hit;
the entire cache is discarded across application builds or format versions.
"""
import base64
import hashlib
import json
import math
import pickle
import re
import zlib
from pathlib import Path

FIELD = 'saved_cleanout_paths'
FORMAT = 1
MAX_ENTRIES = 1024
MAX_RAW_ENTRY = 64 * 1024 * 1024
MAX_RAW_TOTAL = 128 * 1024 * 1024
MAX_COMPRESSED_TOTAL = 32 * 1024 * 1024
MAX_ITEMS = 2_000_000
KINDS = {'raw-fill-region', 'complete-cleanout-path', 'rest-region-loops'}


def build_id():
    from .build_info import load_build_info
    return load_build_info(Path(__file__).resolve().parents[1]).build_id


def owners(project):
    from .model import IslandFillOperation,RestMachiningOperation
    return {hashlib.sha256(op.id.encode()).hexdigest():op.id for op in project.operations
            if type(op) in (IslandFillOperation,RestMachiningOperation)}


def key_owner(key, allowed):
    if not isinstance(key,str):raise ValueError('Invalid cache key')
    parts=key.split(':')
    if (len(parts)!=3 or parts[0] not in KINDS or parts[1] not in allowed
            or not re.fullmatch('[0-9a-f]{64}',parts[2])):
        raise ValueError('Cache is not owned by a cleanout in this job')
    return parts[0],allowed[parts[1]]


def number(value, *, optional=False):
    if optional and value is None:return None
    if type(value) not in (int,float) or not math.isfinite(value) or abs(value)>1e12:
        raise ValueError('Invalid numeric cache value')
    return value


def metadata_pack(value, depth=0):
    if depth>20:raise ValueError('Cache metadata nesting limit')
    if value is None or type(value) in (str,bool,int,float):
        if type(value) in (int,float):number(value)
        return value
    if type(value) is dict:
        if any(type(k) is not str for k in value):raise ValueError('Invalid metadata key')
        return ['dict',[[k,metadata_pack(v,depth+1)] for k,v in value.items()]]
    if type(value) in (list,tuple):
        return ['tuple' if type(value) is tuple else 'list',[metadata_pack(v,depth+1) for v in value]]
    raise ValueError('Unsupported metadata type')


def metadata_unpack(value, depth=0):
    if depth>20:raise ValueError('Cache metadata nesting limit')
    if value is None or type(value) in (str,bool,int,float):
        if type(value) in (int,float):number(value)
        return value
    if type(value) is not list or len(value)!=2 or type(value[1]) is not list:
        raise ValueError('Invalid metadata structure')
    tag,items=value
    if tag=='dict':
        result={}
        for pair in items:
            if type(pair) is not list or len(pair)!=2 or type(pair[0]) is not str or pair[0] in result:
                raise ValueError('Invalid metadata dictionary')
            result[pair[0]]=metadata_unpack(pair[1],depth+1)
        return result
    if tag not in ('list','tuple'):raise ValueError('Invalid metadata tag')
    result=[metadata_unpack(v,depth+1) for v in items]
    return tuple(result) if tag=='tuple' else result


def pack_value(value, kind, owner):
    from .toolpath import GeneratedToolpath
    if kind=='rest-region-loops':
        return [[[number(p.x),number(p.y)] for p in loop] for loop in value]
    if type(value) is not GeneratedToolpath:raise ValueError('Not a generated cleanout path')
    blocks=[]
    for b in value.blocks:
        if b.operation_id!=owner:raise ValueError('Foreign operation in cache')
        blocks.append([b.operation_id,b.tool_station,b.label,metadata_pack(b.metadata),
            [[m.kind.value,number(m.end.x),number(m.end.y),number(m.end.z),number(m.feed,optional=True),
              [number(m.center.x),number(m.center.y)] if m.center else None,number(m.dwell_seconds,optional=True)] for m in b.motions]])
    return blocks


def unpack_value(data, kind, owner):
    from .model import Point
    from .toolpath import GeneratedToolpath,ToolpathBlock,ToolPoint,Motion,MotionKind
    if type(data) is not list or len(data)>MAX_ITEMS:raise ValueError('Invalid cache array')
    total=0
    if kind=='rest-region-loops':
        loops=[]
        for loop in data:
            if type(loop) is not list:raise ValueError('Invalid loop')
            total+=len(loop)
            if total>MAX_ITEMS:raise ValueError('Too many loop points')
            points=[]
            for pair in loop:
                if type(pair) is not list or len(pair)!=2:raise ValueError('Invalid point')
                points.append(Point(number(pair[0]),number(pair[1])))
            loops.append(points)
        return loops
    blocks=[]
    for b in data:
        if (type(b) is not list or len(b)!=5 or b[0]!=owner or type(b[1]) is not int
                or not 1<=b[1]<=64 or type(b[2]) is not str or type(b[4]) is not list):
            raise ValueError('Invalid toolpath block')
        meta=metadata_unpack(b[3])
        if type(meta) is not dict:raise ValueError('Invalid block metadata')
        total+=len(b[4])
        if total>MAX_ITEMS:raise ValueError('Too many motions')
        motions=[]
        for m in b[4]:
            if type(m) is not list or len(m)!=7:raise ValueError('Invalid motion')
            center=m[5]
            if center is not None:
                if type(center) is not list or len(center)!=2:raise ValueError('Invalid arc center')
                center=Point(number(center[0]),number(center[1]))
            motions.append(Motion(MotionKind(m[0]),ToolPoint(number(m[1]),number(m[2]),number(m[3])),
                                  number(m[4],optional=True),center,number(m[6],optional=True)))
        blocks.append(ToolpathBlock(b[0],b[1],motions,b[2],meta))
    return GeneratedToolpath(blocks)


def matching_entries(project, entries):
    """Collect current keys through the normal validators/classifier, without CAM.

    Reusing operation IDs (Save Copy, Undo, or duplicate jobs) is not sufficient
    ownership evidence. Visit current keys and return empty stand-ins at the
    expensive fill/rest-region boundaries. No offsets, motion or audits are
    calculated, and no results are published into the real session cache.
    """
    from .calculation_cache import CalculationCache,calculation_session
    from .model import IslandFillOperation,RestMachiningOperation
    from .stock import generate_operation_toolpath
    from .toolpath import GeneratedToolpath
    allowed=owners(project);eligible={}
    for key,value in entries:
        try:key_owner(key,allowed)
        except ValueError:continue
        eligible[key]=value
    if not eligible:return []
    class KeyVisitor(CalculationCache):
        def __init__(self):
            super().__init__();self.matches=set()
        def compute(self,key,calculate):
            kind,_=key_owner(key,allowed)
            if key in eligible:self.matches.add(key)
            if kind=='complete-cleanout-path':return calculate()
            if kind=='raw-fill-region':return GeneratedToolpath([])
            if kind=='rest-region-loops':return []
            raise ValueError('Unsupported cache-key visitor')
    visitor=KeyVisitor()
    with calculation_session(visitor):
        for op in project.operations:
            if type(op) not in (IslandFillOperation,RestMachiningOperation):continue
            for optimize in (False,True):
                try:generate_operation_toolpath(project,op,optimize=optimize)
                except (ValueError,TypeError,KeyError,OverflowError):pass
    return [(key,value) for key,value in eligible.items() if key in visitor.matches]


def export_cache(project, entries):
    """Read only trusted in-process pickles; serialize data, never executable objects."""
    allowed=owners(project);records=[];raw_total=compressed_total=0
    for key,value in reversed(matching_entries(project,entries)):
        if len(records)>=MAX_ENTRIES:break
        try:
            kind,owner=key_owner(key,allowed)
            # Entries originate in our worker or the JSON decoder below.
            raw=json.dumps(pack_value(pickle.loads(value),kind,owner),separators=(',',':'),allow_nan=False).encode()
            if len(raw)>MAX_RAW_ENTRY or raw_total+len(raw)>MAX_RAW_TOTAL:continue
            zipped=zlib.compress(raw,level=3)
            if compressed_total+len(zipped)>MAX_COMPRESSED_TOTAL:continue
            records.append({'key':key,'bytes':len(raw),'sha256':hashlib.sha256(raw).hexdigest(),
                            'data':base64.b64encode(zipped).decode('ascii')})
            raw_total+=len(raw);compressed_total+=len(zipped)
        except (ValueError,TypeError,KeyError,AttributeError,OverflowError,RecursionError,pickle.UnpicklingError,EOFError):
            continue  # Optional acceleration must not prevent saving the design.
    return {'format':FORMAT,'build':build_id(),'entries':list(reversed(records))} if records else None


def import_cache(project, payload):
    """Fail closed per entry. Disk bytes only enter base64/zlib/JSON parsers."""
    if payload is None:return [],''
    if (type(payload) is not dict or type(payload.get('format')) is not int
            or payload.get('format')!=FORMAT or payload.get('build')!=build_id()):
        return [],'Saved cleanout cache ignored: different Workbench build or cache format; normal regeneration required.'
    records=payload.get('entries')
    if type(records) is not list or len(records)>MAX_ENTRIES:
        return [],'Saved cleanout cache ignored: invalid entry list; normal regeneration required.'
    allowed=owners(project);result=[];seen=set();raw_total=compressed_total=memory_total=0;rejected=0
    from .calculation_cache import MAX_BYTES
    for index,r in enumerate(records):
        if raw_total>=MAX_RAW_TOTAL or compressed_total>=MAX_COMPRESSED_TOTAL or memory_total>=MAX_BYTES:
            rejected+=len(records)-index
            break
        try:
            if type(r) is not dict:raise ValueError('Invalid record')
            key=r['key'];kind,owner=key_owner(key,allowed)
            size=r['bytes'];encoded=r['data']
            if key in seen or type(size) is not int or not 0<size<=MAX_RAW_ENTRY or raw_total+size>MAX_RAW_TOTAL:
                raise ValueError('Invalid or excessive cache size')
            if type(encoded) is not str or len(encoded)>4*((MAX_COMPRESSED_TOTAL+2)//3):raise ValueError('Excessive compressed cache')
            zipped=base64.b64decode(encoded,validate=True);compressed_total+=len(zipped)
            if compressed_total>MAX_COMPRESSED_TOTAL:raise ValueError('Compressed cache budget exhausted')
            decoder=zlib.decompressobj();raw=decoder.decompress(zipped,size+1)
            if len(raw)!=size or not decoder.eof or decoder.unused_data or decoder.unconsumed_tail:
                raise ValueError('Invalid compressed length')
            raw_total+=size
            if hashlib.sha256(raw).hexdigest()!=r['sha256']:raise ValueError('Damaged cache checksum')
            data=json.loads(raw)
            value=unpack_value(data,kind,owner)
            # This pickle is constructed locally from validated primitive data;
            # no pickle from the saved document is ever executed.
            internal=pickle.dumps(value,protocol=5);memory_total+=len(internal)
            if memory_total>MAX_BYTES:raise ValueError('Memory cache budget exhausted')
            result.append((key,internal));seen.add(key)
        except (ValueError,TypeError,KeyError,OverflowError,RecursionError,zlib.error,UnicodeError):
            rejected+=1
    note=f'Loaded {len(result)} saved cleanout cache entries; reuse requires matching inputs. Full output checks still run.'
    if rejected:note+=f' {rejected} invalid/inapplicable entries ignored; missing paths regenerate normally.'
    return result,note


def restore_into_app(app, project):
    payload=project.__dict__.pop('_saved_cleanout_cache',None)
    entries,note=import_cache(project,payload)
    if entries:
        from .calculation_cache import CalculationCache
        cache=CalculationCache([*entries,*app.__dict__.get('_calculation_cache_entries',[])])
        app._calculation_cache_entries=list(cache.entries.items())
    return note
