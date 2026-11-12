"""Policy-specific regression checks for the isolated candidate-scoring hook."""
import unittest
from verify_nesting import ContactNester, SearchStopped, pc, part, L, BOX, HOLE, SMALL, validate
from score_trial import fixture, quality


class Scores(unittest.TestCase):
    def test_default_layout_unchanged(self):
        parts=fixture('E12')
        default=ContactNester(pc).nest(parts,24,24,angles=(0,90,180))
        explicit=ContactNester(pc,scoring='area').nest(parts,24,24,angles=(0,90,180))
        self.assertEqual(default,explicit)
        self.assertEqual(quality(default),(2,543.7556100144))

    def test_invalid_policy(self):
        with self.assertRaises(ValueError): ContactNester(pc,scoring='guess')

    def test_all_policies_holes_compounds_and_units(self):
        source=[part('ring',BOX,[HOLE]),part('insert',SMALL),
                part('rigid',SMALL,[[(x+5,y) for x,y in SMALL]]),part('L',L)]
        original=repr(source)
        for policy in ('area','height','width'):
            for scale in (1,25.4):
                from contact_nesting import Part
                parts=[Part(p.key,tuple(tuple((x*scale,y*scale) for x,y in q)
                                       for q in p.paths)) for p in source]
                a=ContactNester(pc,scoring=policy).nest(parts,24*scale,24*scale,
                           spacing=.25*scale,angles=(0,90,180))
                b=ContactNester(pc,scoring=policy).nest(parts,24*scale,24*scale,
                           spacing=.25*scale,angles=(0,90,180))
                validate(a,24*scale,24*scale,.25*scale)
                self.assertEqual(a,b)
                self.assertEqual({p.key for p in a},{p.key for p in source})
        self.assertEqual(original,repr(source))

    def test_policy_failures_remain_closed(self):
        for policy in ('area','height','width'):
            with self.assertRaises(SearchStopped):
                ContactNester(pc,scoring=policy,cancelled=lambda:True).nest([part('L',L)],24,24)
            with self.assertRaises(ValueError):
                ContactNester(pc,scoring=policy).nest([part('big',BOX)],5,5)
            with self.assertRaises(SearchStopped):
                ContactNester(pc,scoring=policy,timeout=-1).nest([part('L',L)],24,24)

    def test_best_validated_trial_preserves_baseline_quality(self):
        # An alternative may be worse; retaining the baseline is mandatory.
        for name in ('L12','E12','mixed24'):
            parts=fixture(name)
            layouts=[]
            for policy in ('area','height','width'):
                layout=ContactNester(pc,scoring=policy).nest(parts,24,24,angles=(0,90,180))
                validate(layout,24,24,.25)
                layouts.append(layout)
            winner=min(layouts,key=quality)
            self.assertLessEqual(quality(winner),quality(layouts[0]))
            if name=='mixed24': self.assertLess(quality(winner),quality(layouts[0]))


if __name__=='__main__': unittest.main()
