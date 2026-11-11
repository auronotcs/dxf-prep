import 'dart:convert';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:workbench_prototype/design/controller.dart';
import 'package:workbench_prototype/design/geometry_inspector.dart';
import 'dimension_test.dart' show DimensionModel;

Json entity(String kind, {String id = 'e'}) => {
  'id': id,
  'kind': kind,
  'layer': 'Parts',
  'metadata': <String, dynamic>{},
  'bounds': [0.0, 0.0, 4.0, 3.0],
  'start': {'x': 0.0, 'y': 0.0},
  'end': {'x': 4.0, 'y': 3.0},
  'center': {'x': 2.0, 'y': 1.5},
  'radius': 1.5,
  'corner_a': {'x': 0.0, 'y': 0.0},
  'corner_b': {'x': 4.0, 'y': 3.0},
  'points': [
    {'x': 0.0, 'y': 0.0},
    {'x': 4.0, 'y': 3.0},
  ],
  'closed': false,
  'insertion': {'x': 1.0, 'y': 2.0},
  'text': 'Part A',
  'height': .25,
  'rotation': 30.0,
  'font_family': 'Arial',
  'alignment': 'left',
};
void setSelection(DimensionModel m, List<Json> items) {
  m.state['entities'] = items;
  m.state['selected'] = items.map((e) => e['id']).toList();
  m.state['selectionBounds'] = [0.0, 0.0, 4.0, 3.0];
  m.state['selectionInspection'] = {
    'items': [
      for (final e in items)
        {
          'id': e['id'],
          'closed': ['circle', 'rectangle'].contains(e['kind']),
          'length': e['kind'] == 'text' ? null : 123.4567,
          'area': ['circle', 'rectangle'].contains(e['kind']) ? 9.8765 : null,
        },
    ],
    'totalLength': 246.9134,
    'contourAreaSum': 19.753,
    'lengthCount': 2,
    'areaCount': 2,
    'textCount': 0,
  };
}

void main() {
  Future<void> mount(
    WidgetTester t,
    DesignController m,
    List<String> calls, {
    double width = 246,
  }) async {
    await t.pumpWidget(
      MaterialApp(
        home: Scaffold(
          body: SizedBox(
            width: width,
            child: SingleChildScrollView(
              child: GeometryInspector(model: m, onAction: calls.add),
            ),
          ),
        ),
      ),
    );
    await t.pumpAndSettle();
  }

  testWidgets(
    'Single-object inspector uses core measurements and type-specific coordinates/actions',
    (t) async {
      final m = DimensionModel(), calls = <String>[];
      addTearDown(m.dispose);
      for (final kind in ['line', 'circle', 'rectangle', 'polyline', 'text']) {
        setSelection(m, [entity(kind)]);
        await mount(t, m, calls);
        expect(find.text(kind.toUpperCase()), findsOneWidget);
        expect(find.text('Parts'), findsOneWidget);
        expect(find.text('0.0000, 0.0000 mm'), findsWidgets);
        expect(find.text('4.0000 mm'), findsOneWidget);
        if (kind == 'text') {
          expect(find.text('123.4567 mm'), findsNothing);
          expect(find.textContaining('bounds are approximate'), findsOneWidget);
          expect(find.text('Insertion X / Y'), findsOneWidget);
        } else {
          expect(
            find.text('123.4567 mm'),
            findsOneWidget,
          ); // Never recompute in Dart.
        }
        if (kind == 'circle') {
          expect(find.text('3.0000 mm'), findsWidgets);
          expect(find.text('9.8765 mm²'), findsOneWidget);
        }
        final button = find.widgetWithText(
          TextButton,
          kind == 'text'
              ? 'Edit text'
              : kind == 'circle'
              ? 'Resize circles'
              : 'Edit points',
        );
        await t.ensureVisible(button);
        await t.tap(button);
        await t.pump();
        expect(
          calls.last,
          kind == 'text'
              ? 'editText'
              : kind == 'circle'
              ? 'resizeCircles'
              : 'points',
        );
        expect(t.takeException(), isNull);
      }
      expect(m.calls, isEmpty);
    },
  );
  testWidgets(
    'Multi-selection totals are explicit and all object details can be reached',
    (t) async {
      final m = DimensionModel(), calls = <String>[];
      addTearDown(m.dispose);
      setSelection(
        m,
        List.generate(21, (i) => entity('rectangle', id: 'object-$i')),
      );
      final before = jsonEncode(m.state);
      await mount(t, m, calls);
      expect(find.text('Selection totals · 21 objects'), findsOneWidget);
      expect(find.text('246.9134 mm'), findsOneWidget);
      expect(find.text('19.7530 mm²'), findsOneWidget);
      expect(
        find.textContaining('overlaps and holes are not subtracted'),
        findsOneWidget,
      );
      final more = find.widgetWithText(TextButton, 'Show more (1 remaining)');
      await t.ensureVisible(more);
      await t.tap(more);
      await t.pumpAndSettle();
      final row = find.byKey(const ValueKey('inspect-object-20'));
      await t.ensureVisible(row);
      await t.tap(
        find.descendant(of: row, matching: find.text('rectangle · Parts')),
      );
      await t.pumpAndSettle();
      expect(
        find.descendant(of: row, matching: find.text('Contour area')),
        findsOneWidget,
      );
      expect(jsonEncode(m.state), before);
      expect(m.calls, isEmpty);
      expect(calls, isEmpty);
    },
  );
  testWidgets(
    'Readonly locked hidden and busy inspections never enable edit actions',
    (t) async {
      final m = DimensionModel(), calls = <String>[];
      addTearDown(m.dispose);
      for (final mode in [
        'readonly',
        'locked',
        'hidden',
        'busy',
        'annotation-hidden',
      ]) {
        final e = entity('circle');
        if (mode == 'annotation-hidden') {
          e['metadata'] = {'annotation_hidden': true};
        }
        setSelection(m, [e]);
        m.state['readOnly'] = mode == 'readonly';
        m.busy = mode == 'busy';
        m.state['layers'] = [
          {
            'name': 'Parts',
            'locked': mode == 'locked',
            'visible': mode != 'hidden',
          },
        ];
        await mount(t, m, calls);
        expect(find.text('Radius'), findsOneWidget);
        for (final b in t.widgetList<TextButton>(find.byType(TextButton))) {
          expect(b.onPressed, isNull, reason: mode);
        }
        if (mode == 'locked') expect(find.text('Layer locked'), findsOneWidget);
        if (mode == 'hidden' || mode == 'annotation-hidden') {
          expect(find.text('Hidden'), findsOneWidget);
        }
        expect(t.takeException(), isNull);
      }
      expect(calls, isEmpty);
      expect(m.calls, isEmpty);
    },
  );
  testWidgets(
    'Empty selection and older backend do not fabricate measurements',
    (t) async {
      final m = DimensionModel(), calls = <String>[];
      addTearDown(m.dispose);
      setSelection(m, []);
      await mount(t, m, calls);
      expect(find.textContaining('Click geometry'), findsOneWidget);
      setSelection(m, [entity('circle')]);
      m.state.remove('selectionInspection');
      await mount(t, m, calls);
      expect(find.textContaining('restart Flutter'), findsOneWidget);
      expect(find.text('Perimeter'), findsNothing);
      expect(find.text('Contour area'), findsNothing);
    },
  );
  testWidgets(
    'Actual Python inspection renders inch/mm after edits without changing the drawing',
    (t) async {
      await t.runAsync(() async {
        final m = DesignController(), calls = <String>[];
        try {
          await m.start();
          expect(m.ready, true, reason: m.message);
          for (final units in ['in', 'mm']) {
            await m.run('project.new', {'units': units});
            await m.run('geometry.add_rectangle', {
              'x': -1,
              'y': 2,
              'width': 4,
              'height': 3,
            });
            await m.select({m.entities.first['id']});
            final before = jsonEncode(m.state);
            await mount(t, m, calls, width: 214);
            expect(find.text('14.0000 $units'), findsOneWidget);
            expect(find.text('12.0000 $units²'), findsOneWidget);
            expect(find.text('-1.0000, 2.0000 $units'), findsWidgets);
            expect(jsonEncode(m.state), before);
            await m.run('design.scale', {'sx': 2, 'sy': 2});
            await mount(t, m, calls, width: 214);
            expect(find.text('48.0000 $units²'), findsOneWidget);
            await m.run('history.undo');
            await mount(t, m, calls, width: 214);
            expect(find.text('12.0000 $units²'), findsOneWidget);
            expect(t.takeException(), isNull);
          }
          expect(calls, isEmpty);
        } finally {
          await m.shutdown();
          m.dispose();
        }
      });
    },
  );
}
