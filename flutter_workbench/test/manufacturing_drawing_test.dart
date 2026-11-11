import 'dart:convert';
import 'package:flutter/gestures.dart';
import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:workbench_prototype/design/controller.dart';
import 'package:workbench_prototype/design/canvas.dart';
import 'package:workbench_prototype/design/manufacturing_drawing.dart';
import 'package:workbench_prototype/design/manufacturing_workspace.dart';
import 'manufacturing_expanded_test.dart' show ExpandedModel;

class SelectionModel extends ExpandedModel {
  @override
  Future<bool> run(String command, [Json params = const {}]) async {
    if (command == 'selection.set') {
      calls.add(command);
      state['selected'] = List<String>.from(params['entity_ids']);
      state['revision'] = (state['revision'] as int) + 1;
      notifyListeners();
      return true;
    }
    return super.run(command, params);
  }
}

void main() {
  final canvas = find.byKey(const ValueKey('manufacturing-drawing-canvas'));
  DesignViewport view(WidgetTester t) =>
      (t.state(find.byType(ManufacturingDrawing)) as dynamic).view
          as DesignViewport;
  Offset at(WidgetTester t, double x, double y) =>
      t.getTopLeft(canvas) + view(t).screen(Offset(x, y));
  Future<SelectionModel> mount(WidgetTester t, {bool workspace = true}) async {
    t.view.physicalSize = const Size(1400, 1000);
    t.view.devicePixelRatio = 1;
    addTearDown(t.view.resetPhysicalSize);
    addTearDown(t.view.resetDevicePixelRatio);
    final m = SelectionModel();
    addTearDown(m.dispose);
    m.state['selected'] = <String>[];
    m.state['entities'].addAll(<Json>[
      {
        'id': 'circle',
        'kind': 'circle',
        'layer': '0',
        'center': {'x': 12, 'y': 3},
        'radius': 1,
        'bounds': [11, 2, 13, 4],
      },
      {
        'id': 'line',
        'kind': 'line',
        'layer': '0',
        'start': {'x': 2, 'y': 7},
        'end': {'x': 7, 'y': 7},
        'bounds': [2, 7, 7, 7],
      },
      {
        'id': 'hidden',
        'kind': 'rectangle',
        'layer': 'hidden',
        'corner_a': {'x': 3, 'y': 2},
        'corner_b': {'x': 5, 'y': 4},
        'bounds': [3, 2, 5, 4],
      },
      {
        'id': 'locked',
        'kind': 'rectangle',
        'layer': 'locked',
        'corner_a': {'x': 15, 'y': 2},
        'corner_b': {'x': 17, 'y': 4},
        'bounds': [15, 2, 17, 4],
      },
    ]);
    m.state['layers'] = <Json>[
      {'name': '0', 'visible': true, 'locked': false},
      {'name': 'hidden', 'visible': false, 'locked': false},
      {'name': 'locked', 'visible': true, 'locked': true},
    ];
    m.response = {'status': 'failed', 'error': 'Test draft'};
    await t.pumpWidget(
      MaterialApp(
        home: workspace
            ? ManufacturingWorkspace(
                initialInspectorOpen: true,
                model: m,
                onSave: () async => true,
              )
            : Scaffold(body: ManufacturingDrawing(model: m)),
      ),
    );
    await t.pumpAndSettle();
    return m;
  }

  Future<void> click(WidgetTester t, String text) async {
    if (find.text(text).evaluate().isEmpty) {
      final scroll = find
          .descendant(
            of: find.byType(ListView).first,
            matching: find.byType(Scrollable),
          )
          .first;
      t.state<ScrollableState>(scroll).position.jumpTo(0);
      await t.pumpAndSettle();
      await t.scrollUntilVisible(
        find.text(text),
        200,
        scrollable: find
            .descendant(
              of: find.byType(ListView).first,
              matching: find.byType(Scrollable),
            )
            .first,
      );
    }
    await t.ensureVisible(find.text(text));
    await t.pumpAndSettle();
    await t.ensureVisible(find.text(text));
    await t.pumpAndSettle();
    await t.tap(find.text(text));
    await t.pumpAndSettle();
  }

  testWidgets(
    'Drawing is visible immediately and graphical selection feeds profile authoring',
    (t) async {
      final m = await mount(t);
      final before = jsonEncode(m.entities);
      expect(canvas, findsOneWidget);
      expect(
        find.text('Generate toolpaths to inspect the actual cutter motion.'),
        findsNothing,
      );
      await t.tapAt(at(t, 4, 3));
      await t.pumpAndSettle();
      expect(m.selection, {'entity_123456'});
      expect(find.byTooltip('1 selected'), findsOneWidget);
      await click(t, 'Add profile');
      await click(t, 'Apply');
      expect(m.applied!['entity_ids'], ['entity_123456']);
      expect(m.calls.first, 'selection.set');
      expect(m.calls, isNot(contains('manufacturing.generate')));
      expect(jsonEncode(m.entities), before);
      expect(t.takeException(), isNull);
    },
  );
  testWidgets(
    'Shift toggles, box selects, hidden and locked contours stay excluded',
    (t) async {
      final m = await mount(t, workspace: false);
      await t.tapAt(at(t, 4, 3));
      await t.pumpAndSettle();
      await t.sendKeyDownEvent(LogicalKeyboardKey.shiftLeft);
      await t.tapAt(at(t, 12, 3));
      await t.pumpAndSettle();
      expect(m.selection, {'entity_123456', 'circle'});
      await t.tapAt(at(t, 4, 3));
      await t.pumpAndSettle();
      expect(m.selection, {'circle'});
      await t.sendKeyUpEvent(LogicalKeyboardKey.shiftLeft);
      final start = at(t, 1.6, 1.6), end = at(t, 17.4, 7.4);
      await t.dragFrom(start, end - start);
      await t.pumpAndSettle();
      expect(m.selection, {'entity_123456', 'circle', 'line'});
      await t.tapAt(at(t, 16, 3));
      await t.pumpAndSettle();
      expect(m.selection, isEmpty);
      expect(m.calls.every((c) => c == 'selection.set'), isTrue);
      expect(m.dirty, isFalse);
      expect(t.takeException(), isNull);
    },
  );
  testWidgets(
    'Panning and zooming retain geometry and screen-correct open-line picking',
    (t) async {
      final m = await mount(t, workspace: false);
      final before = jsonEncode(m.entities);
      final v = view(t);
      final translation = v.translation;
      final g = await t.startGesture(
        at(t, 8, 5),
        kind: PointerDeviceKind.mouse,
        buttons: kMiddleMouseButton,
      );
      await g.moveBy(const Offset(40, 20));
      await g.up();
      await t.pumpAndSettle();
      expect(v.translation, translation + const Offset(40, 20));
      expect(m.calls, isEmpty);
      final anchor = at(t, 4, 7), scale = v.scale;
      await t.sendEventToBinding(
        PointerScrollEvent(
          position: anchor,
          scrollDelta: const Offset(0, -150),
        ),
      );
      await t.pumpAndSettle();
      expect(v.scale, greaterThan(scale));
      await t.tapAt(at(t, 4, 7));
      await t.pumpAndSettle();
      expect(m.selection, {'line'});
      expect(jsonEncode(m.entities), before);
      expect(t.takeException(), isNull);
    },
  );
  testWidgets(
    'Canceled and stale selection gestures do not change selected geometry',
    (t) async {
      final m = await mount(t, workspace: false);
      final g = await t.startGesture(at(t, 1.6, 1.6));
      await g.moveTo(at(t, 14, 8));
      await t.sendKeyEvent(LogicalKeyboardKey.escape);
      await g.up();
      await t.pumpAndSettle();
      expect(m.calls, isEmpty);
      final h = await t.startGesture(at(t, 1.6, 1.6));
      await h.moveTo(at(t, 14, 8));
      m.externalEdit();
      await t.pumpAndSettle();
      await h.up();
      await t.pumpAndSettle();
      expect(m.calls, isEmpty);
      m.busy = true;
      m.notifyListeners();
      await t.pumpAndSettle();
      await t.tapAt(at(t, 4, 3));
      await t.pumpAndSettle();
      expect(m.calls, isEmpty);
      expect(t.takeException(), isNull);
    },
  );
  testWidgets(
    'New operation filters incompatible selections without requiring toolpaths',
    (t) async {
      final m = await mount(t);
      m.state['selected'] = <String>['entity_123456', 'line'];
      m.notifyListeners();
      await t.pumpAndSettle();
      await click(t, 'Add Cut Line');
      await click(t, 'Apply');
      expect(m.requested!['params']['entity_ids'], ['line']);
      expect(m.requestedCommand, 'manufacturing.operation_prepare');
      expect(m.calls, isEmpty);
      expect(t.takeException(), isNull);
    },
  );
  testWidgets(
    'Drawing and toolpath views switch while retaining selection and viewport',
    (t) async {
      final m = await mount(t);
      await t.tapAt(at(t, 12, 3));
      await t.pumpAndSettle();
      final original = view(t).translation;
      await click(t, 'Toolpaths');
      expect(canvas, findsNothing);
      expect(
        find.text('Generate toolpaths to inspect the actual cutter motion.'),
        findsOneWidget,
      );
      await click(t, 'Drawing');
      expect(canvas, findsOneWidget);
      expect(view(t).translation, original);
      expect(m.selection, {'circle'});
      expect(t.takeException(), isNull);
    },
  );
}
