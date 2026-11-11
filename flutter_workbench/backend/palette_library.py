"""Isolated palette resources; browsing/import never changes a drawing."""
import hashlib
import json
from pathlib import Path
from cam_core.color_palettes import read_palette, palette_from_dict, merge_palette_records
from cam_core.model import new_id


class PaletteLibrary:
    def __init__(self, path):
        self.path = Path(path)
        self.pending_import = None
        self.colors = {}

    def read(self):
        if not self.path.exists(): return []
        records = json.loads(self.path.read_text(encoding='utf-8'))
        if not isinstance(records, list): raise ValueError('Stored palettes must be a list.')
        return [palette_from_dict(record) for record in records]

    def digest(self):
        return hashlib.sha256(self.path.read_bytes() if self.path.exists() else b'').hexdigest()

    def write(self, records):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix('.tmp')
        temporary.write_text(json.dumps(records, ensure_ascii=False, allow_nan=False), encoding='utf-8')
        temporary.replace(self.path)

    def command(self, command, p):
        if command == 'palette.list':
            return {'digest':self.digest(), 'palettes': [dict(id=x.id,name=x.name,manufacturer=x.manufacturer,collection=x.collection,
                version=x.version,source=x.source,swatches=[dict(id=s.id,name=s.name,product_code=s.product_code,
                color=x.swatch_snapshot(s).to_dict()) for s in x.swatches]) for x in self.read()]}
        if command == 'palette.import_preview':
            self.pending_import = None
            palette = read_palette(Path(p['path']))
            existing = next((x for x in self.read() if x.id.casefold()==palette.id.casefold()), None)
            token = new_id('palette-import')
            self.pending_import = (token, self.digest(), palette)
            return dict(token=token,name=palette.name,manufacturer=palette.manufacturer,
                        count=len(palette.swatches),replaces=existing.name if existing else None)
        if command == 'palette.import_apply':
            pending = self.pending_import
            if not pending or p.get('token') != pending[0] or self.digest() != pending[1]:
                raise ValueError('The palette library changed. Import the file again to review it.')
            records, _ = merge_palette_records([x.to_dict() for x in self.read()], pending[2])
            self.write(records)
            self.pending_import = None
            return {'name':pending[2].name}
        if command == 'palette.choose':
            palette = next((x for x in self.read() if x.id==p.get('palette_id')), None)
            if palette is None: raise ValueError('That palette is no longer available. Refresh the browser.')
            color = palette.swatch_snapshot(p['swatch_id'])
            token = new_id('palette-color')
            if len(self.colors) >= 256: self.colors.pop(next(iter(self.colors)))
            self.colors[token] = color
            return {'color':color.to_dict(),'token':token}
        if command == 'palette.remove':
            if p.get('digest') != self.digest(): raise ValueError('The palette library changed. Refresh before removing a palette.')
            palettes = self.read()
            if not any(x.id == p.get('palette_id') for x in palettes): raise ValueError('Palette no longer exists.')
            self.write([x.to_dict() for x in palettes if x.id != p['palette_id']])
            return {}
        raise ValueError('Unknown palette command.')

    def selected_color(self, token, candidate):
        color = self.colors.get(token)
        if color is None or color != candidate:
            raise ValueError('The chosen swatch changed. Choose it again from the palette browser.')
        return color
