"""Non-mutating, reviewed PDF export using the captured shop-drawing engine."""
from dataclasses import asdict
from pathlib import Path
import os
import shutil
import tempfile

from cam_core.plates import make_plate_project
from cam_core.shop_drawing import ShopDrawingPage, ShopDrawingSettings, render_shop_drawing_pdf


class ShopPDF:
    def __init__(self, production_root):
        self.production_root = Path(production_root).resolve()
        self.cache = None
        self.temp = None

    def clear(self):
        self.cache = None
        if self.temp is not None:
            self.temp.cleanup()
            self.temp = None

    def prepare(self, project, plates, params, *, revision, digest):
        self.clear()
        numbers = params.get('plate_numbers')
        known = {p.number for p in plates}
        if (not isinstance(numbers,list) or not numbers or
            any(type(n) is not int for n in numbers) or len(set(numbers))!=len(numbers) or not set(numbers)<=known):
            raise ValueError('Select at least one existing plate; each plate can be selected once.')
        settings = ShopDrawingSettings(page_size=params.get('page_size','Tabloid'),
            orientation=params.get('orientation','landscape'),scale_mode=params.get('scale_mode','fit'),
            scale_denominator=float(params.get('scale_denominator',1)))
        settings.validate()
        pages, included, crossing = [], set(), set()
        for plate in plates:
            if plate.number not in numbers: continue
            extracted = make_plate_project(project,plate)
            included.update(e.id for e in extracted.project.entities if not e.metadata.get('annotation_hidden',False))
            crossing.update(extracted.crossing_entity_ids)
            pages.append(ShopDrawingPage(f'Plate {plate.number} - {plate.width:g} x {plate.height:g} {project.units}',
                extracted.project,plate.width,plate.height))
        self.temp = tempfile.TemporaryDirectory(prefix='flutter-shop-pdf-')
        staged = Path(self.temp.name)/'drawing.pdf'
        try:
            result = render_shop_drawing_pdf(staged,project.name,pages,settings)
        except Exception:
            self.clear()
            raise
        visible = {e.id for e in project.entities if not e.metadata.get('annotation_hidden',False)}
        token = os.urandom(16).hex()
        self.cache = dict(token=token,revision=revision,digest=digest,path=staged)
        return dict(token=token,pages=[asdict(page) for page in result.pages],
            omittedCount=len(visible-included),crossingCount=len(crossing-included),
            unresolvedCount=sum(page.unresolved_dimension_count for page in result.pages),settings=asdict(settings))

    def export(self, params, *, revision, digest):
        cached = self.cache
        if not cached or params.get('token')!=cached['token'] or revision!=cached['revision'] or digest!=cached['digest']:
            raise ValueError('The drawing changed. Prepare the PDF again before saving.')
        path = Path(params['path']).resolve()
        if path.suffix.lower()!='.pdf': raise ValueError('Choose a .pdf file.')
        if path.is_relative_to(self.production_root):
            raise ValueError('Choose an evaluation destination outside the live CAM Workbench folder.')
        if not path.parent.is_dir(): raise ValueError('Choose an existing output folder.')
        if path.is_dir(): raise ValueError('Choose a PDF file, not a folder.')
        # Stage beside the destination so errors cannot truncate a previous PDF.
        fd, name = tempfile.mkstemp(prefix='.shop-drawing-',suffix='.tmp',dir=path.parent)
        try:
            with os.fdopen(fd,'wb') as target, cached['path'].open('rb') as source:
                shutil.copyfileobj(source,target)
                target.flush()
                os.fsync(target.fileno())
            os.replace(name,path)
        finally:
            Path(name).unlink(missing_ok=True)
        return dict(path=str(path),bytes=path.stat().st_size)
