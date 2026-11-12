"""Independent clearance and invariants for experimental part ordering."""
import unittest
from verify_nesting import ContactNester, SearchStopped, pc, part, L, validate
from contact_nesting import Part
from score_trial import fixture, quality

POLICIES=('area','input','longest','box_area')

class Orders(unittest.TestCase):
    def test_default_and_identical_parts_unchanged(self):
        parts=fixture('E36')
        default=ContactNester(pc).nest(parts,24,24,angles=(0,90,180))
        self.assertEqual(quality(default),(4,1631.2694400576))
        for policy in POLICIES:
            self.assertEqual(default,ContactNester(pc,ordering=policy).nest(
                parts,24,24,angles=(0,90,180)))

    def test_input_order_is_explicit_and_source_immutable(self):
        parts=[part('z',L),part('a',L)]
        before=repr(parts)
        layout=ContactNester(pc,ordering='input').nest(parts,24,24)
        self.assertEqual([p.key for p in layout],['z','a'])
        validate(layout,24,24,.25)
        self.assertEqual(before,repr(parts))

    def test_unique_holes_rigid_curve_units_determinism(self):
        source=fixture('mixed9')
        before=repr(source)
        for scale in (1,25.4):
            parts=[Part(p.key,tuple(tuple((x*scale,y*scale) for x,y in q)
                                   for q in p.paths)) for p in source]
            for policy in POLICIES:
                layouts=[ContactNester(pc,ordering=policy).nest(parts,24*scale,24*scale,
                    spacing=.25*scale,angles=(0,90,180)) for _ in range(2)]
                validate(layouts[0],24*scale,24*scale,.25*scale)
                self.assertEqual(layouts[0],layouts[1])
                self.assertEqual(sorted(p.key for p in layouts[0]),sorted(p.key for p in parts))
                self.assertTrue(all(p.angle in (0,90,180) for p in layouts[0]))
        self.assertEqual(before,repr(source))

    def test_failure_paths(self):
        with self.assertRaises(ValueError): ContactNester(pc,ordering='random')
        for policy in POLICIES:
            with self.assertRaises(SearchStopped):
                ContactNester(pc,ordering=policy,cancelled=lambda:True).nest([part('L',L)],24,24)
            with self.assertRaises(SearchStopped):
                ContactNester(pc,ordering=policy,timeout=-1).nest([part('L',L)],24,24)
            with self.assertRaises(ValueError):
                ContactNester(pc,ordering=policy).nest([part('L',L)],1,1)

    def test_keep_baseline_even_when_alternatives_regress(self):
        for name in ('mixed9','mixed24'):
            layouts=[]
            for policy in POLICIES:
                layout=ContactNester(pc,ordering=policy).nest(fixture(name),24,24,angles=(0,90,180))
                validate(layout,24,24,.25)
                layouts.append(layout)
            self.assertLessEqual(quality(min(layouts,key=quality)),quality(layouts[0]))

if __name__=='__main__': unittest.main()
