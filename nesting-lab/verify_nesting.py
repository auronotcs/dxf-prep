import sys,json,time,math,unittest
from pathlib import Path
sys.path.insert(0,'C:/Users/auron/Documents/CAM Workbench')
from cam_core._vendor import pyclipper as pc
from cam_core import nesting as old
from cam_core.model import Point,Polyline
from cam_core.plates import Plate
from contact_nesting import ContactNester,Part,SearchStopped

def part(key,path,holes=()):return Part(key,tuple(tuple(p) for p in [path,*holes]))
E=[(0,0),(6,0),(6,1),(1,1),(1,3),(5,3),(5,4),(1,4),(1,6),(6,6),(6,7),(0,7)]
L=[(0,0),(6,0),(6,1.5),(1.5,1.5),(1.5,6),(0,6)]
BOX=[(0,0),(10,0),(10,10),(0,10)]
HOLE=[(2,2),(8,2),(8,8),(2,8)]
SMALL=[(0,0),(3,0),(3,3),(0,3)]

def footprint(paths):
    pts=[[Point(x,y) for x,y in p] for p in paths]
    flat=[q for p in pts for q in p]
    return old._ShapeFootprint([], (min(p.x for p in flat),min(p.y for p in flat),max(p.x for p in flat),max(p.y for p in flat)),pts,[])

def validate(results,width,height,gap):
    for i,a in enumerate(results):
        fa=footprint(a.paths)
        assert fa.bounds[0]>=gap-1e-7 and fa.bounds[1]>=gap-1e-7
        assert fa.bounds[2]<=width-gap+1e-7 and fa.bounds[3]<=height-gap+1e-7
        for b in results[:i]:
            if a.sheet==b.sheet:
                assert old._footprints_clear(fa,footprint(b.paths),gap), (a.key,b.key)

class Tests(unittest.TestCase):
    def run_case(self,parts,w=24,h=24,gap=.25,angles=(0,90,180)):
        engine=ContactNester(pc,timeout=15)
        result=engine.nest(parts,w,h,spacing=gap,angles=angles)
        validate(result,w,h,gap)
        self.assertEqual({p.key for p in parts},{p.key for p in result})
        return result,engine
    def test_concave_repeated(self): self.run_case([part(str(i),E) for i in range(10)])
    def test_hole(self):
        result,_=self.run_case([part('ring',BOX,[HOLE]),part('insert',SMALL)],10.5,10.5)
        self.assertEqual({p.sheet for p in result},{0})
        insert=next(p for p in result if p.key=='insert')
        self.assertGreater(insert.x,2);self.assertLess(insert.x+3,8.5)
    def test_disjoint_compound(self): self.run_case([part('rigid',SMALL,[[(x+6,y) for x,y in SMALL]]),part('L',L)])
    def test_reproducible(self):
        parts=[part(str(i),L) for i in range(8)]
        a,_=self.run_case(parts);b,_=self.run_case(parts);self.assertEqual(a,b)
    def test_impossible(self):
        with self.assertRaises(ValueError): self.run_case([part('big',BOX)],5,5)
    def test_exact_sheet_fit(self): self.run_case([part('square',SMALL)],3.5,3.5)
    def test_zero_gap(self): self.run_case([part(str(i),L) for i in range(5)],gap=0)
    def test_cancel(self):
        with self.assertRaises(SearchStopped):ContactNester(pc,cancelled=lambda:True).nest([part('L',L)],24,24)
    def test_complexity_limit(self):
        with self.assertRaises(SearchStopped):ContactNester(pc,max_pair_cells=1).nest([part('a',E),part('b',E)],24,24)
    def test_units(self):
        source=[part(str(i),L) for i in range(4)]
        inch,_=self.run_case(source)
        mm=[Part(p.key,tuple(tuple((x*25.4,y*25.4) for x,y in q) for q in p.paths)) for p in source]
        metric,_=self.run_case(mm,24*25.4,24*25.4,.25*25.4)
        self.assertEqual(len(set(p.sheet for p in inch)),len(set(p.sheet for p in metric)))
    def test_slanted(self): self.run_case([part(str(i),[(0,0),(5,0),(3,7),(1,5)]) for i in range(8)],angles=(0,30,90,180))
    def test_invalid_bowtie(self):
        with self.assertRaises(ValueError):self.run_case([part('bad',[(0,0),(4,4),(0,4),(4,0)])])
    def test_curved_polygon_samples(self):
        circle=[(3*math.cos(i*2*math.pi/48),3*math.sin(i*2*math.pi/48)) for i in range(48)]
        self.run_case([part(str(i),circle) for i in range(5)],angles=(0,))
    def test_seeded_mixed_shapes(self):
        import random
        randomizer=random.Random(7441)
        for case in range(12):
            parts=[]
            for i in range(9):
                sx=randomizer.uniform(.5,1.5);sy=randomizer.uniform(.5,1.5)
                path=E if i%2 else L
                parts.append(part(str(i),[(x*sx,y*sy) for x,y in path]))
            self.run_case(parts,angles=(0,90,180))

def benchmark(mode,count,kind):
    path=E if kind=='E' else L
    parts=[part(f'p{i:03}',path) for i in range(count)]
    start=time.perf_counter()
    if mode=='contact':
        engine=ContactNester(pc,timeout=30)
        result=engine.nest(parts,24,24,spacing=.25,angles=(0,90,180))
        elapsed=time.perf_counter()-start
        validate(result,24,24,.25)
        sheets=len(set(p.sheet for p in result))
        used=sum(max(x for p in result if p.sheet==s for q in p.paths for x,y in q)*max(y for p in result if p.sheet==s for q in p.paths for x,y in q) for s in range(sheets))
        extra=engine.stats
    else:
        entities=[Polyline(id=p.key,points=[Point(x,y) for x,y in p.paths[0]],closed=True) for p in parts]
        result=old.nest_entities_to_plates(entities,[Plate(1,0,0,24,24)],spacing=.25,allow_rotation=True,automatic_compounds=False,placement_strategy=mode,rotation_step_degrees=90,maximum_rotation_degrees=180,search_resolution=.125)
        elapsed=time.perf_counter()-start
        sheets=len(set(result.assignments.values()));used=0
        for plate in result.plates:
            group=[e for e in result.entities if result.assignments.get(e.id)==plate.number]
            if group:used+=(max(p.x for e in group for p in e.points)-plate.x)*(max(p.y for e in group for p in e.points)-plate.y)
        extra={}
    print(json.dumps(dict(mode=mode,count=count,shape=kind,seconds=elapsed,sheets=sheets,envelope_area=used,**extra)))

if __name__=='__main__':
    if len(sys.argv)>1 and sys.argv[1]=='benchmark':benchmark(sys.argv[2],int(sys.argv[3]),sys.argv[4])
    else:unittest.main()
