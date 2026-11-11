import 'dart:convert';
import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:workbench_prototype/design/controller.dart';
import 'package:workbench_prototype/design/point_editor.dart';
import 'package:workbench_prototype/design/canvas.dart';
import 'package:workbench_prototype/design/workspace.dart';

class PointTestModel extends DesignController {
  final calls = <Json>[];
  PointTestModel() {
    ready = true;
    busy = false;
    state = {
      'revision': 0,
      'capabilities': ['graphical_points'],
      'selected': ['contour'],
      'layers': <Json>[],
      'plates': <Json>[],
      'entities': <Json>[
        {
          'id': 'contour',
          'kind': 'polyline',
          'layer': '0',
          'closed': true,
          'points': [
            {'x': 0, 'y': 0},
            {'x': 10, 'y': 0},
            {'x': 10, 'y': 10},
            {'x': 0, 'y': 10},
          ],
          'bounds': [0, 0, 10, 10],
        },
      ],
    };
  }
  @override
  Future<bool> run(String command, [Json params = const {}]) async {
    calls.add({'command': command, ...params});
    if (command == 'selection.set') state['selected'] = params['entity_ids'];
    state['revision'] = (state['revision'] as int) + 1;
    notifyListeners();
    return true;
  }

  @override
  Future<void> savePreferences(Json value) async {}
  void externalChange() {
    state['revision']++;
    notifyListeners();
  }
}

void main() {
  test(
    'Real backend: preview, multi-point commit, undo, insertion and deletion guards',
    () async {
      final model = DesignController();
      final editor = PointEditor(model);
      try {
        await model.start();
        expect(model.ready, isTrue, reason: model.message);
        expect(
          await model.run('geometry.add_rectangle', {
            'x': 0,
            'y': 0,
            'width': 10,
            'height': 5,
          }),
          isTrue,
        );
        final id = model.entities.single['id'] as String;
        editor.start();
        await editor.activate(id);
        expect(editor.points.length, 4);
        final history = model.state['history']['index'] as int;
        final original = jsonEncode(model.entities);
        editor.press(0, Offset.zero, Offset.zero, additive: true);
        editor.press(
          3,
          const Offset(0, 50),
          const Offset(0, 5),
          additive: true,
        );
        editor.press(0, Offset.zero, Offset.zero);
        editor.motion(
          const Offset(21, -13),
          const Offset(2.1, 1.3),
          snap: true,
        );
        expect(editor.displayed[0], const Offset(2, 1));
        expect(editor.displayed[3], const Offset(2, 6));
        expect(jsonEncode(model.entities), original);
        await editor.release(inside: true);
        expect(model.state['history']['index'], history + 1);
        expect(point(model.entities.single['points'][0]), const Offset(2, 1));
        expect(await model.run('history.undo'), isTrue);
        expect(editor.source, isNull);
        expect(jsonEncode(model.entities), original);
        await editor.activate(id);
        editor.selectAll();
        await editor.edit('delete');
        expect(model.entities.length, 1);
        expect(editor.points.length, 4);
        expect(editor.hint, contains('at least 3'));
        await editor.edit('insert', {'x': -1, 'y': 2});
        expect(editor.points.length, 5);
        editor.press(4, Offset.zero, editor.points[4]);
        await editor.edit('delete');
        expect(editor.points.length, 4);
        expect(model.entities.single['id'], id);
        editor.press(0, Offset.zero, Offset.zero);
        editor.motion(const Offset(20, 0), const Offset(2, 0), snap: false);
        final before = jsonEncode(model.entities);
        editor.cancelDrag();
        await editor.release(inside: true);
        expect(jsonEncode(model.entities), before);
        editor.press(0, Offset.zero, Offset.zero);
        editor.motion(const Offset(20, 0), const Offset(2, 0), snap: false);
        await editor.release(inside: false);
        expect(jsonEncode(model.entities), before);
      } finally {
        editor.dispose();
        await model.shutdown();
        model.dispose();
      }
    },
  );

  testWidgets(
    'Canvas handles move selected vertices with world coordinates and one commit',
    (tester) async {
      final model = PointTestModel();
      final editor = PointEditor(model);
      final view = DesignViewport()
        ..scale = 10
        ..translation = const Offset(50, 180)
        ..tool = 'points';
      addTearDown(model.dispose);
      addTearDown(editor.dispose);
      addTearDown(view.dispose);
      editor.start();
      await editor.activate('contour');
      model.calls.clear();
      await tester.pumpWidget(
        MaterialApp(
          home: SizedBox.expand(
            child: DesignCanvas(
              model: model,
              view: view,
              layer: '0',
              dark: false,
              pointEditor: editor,
            ),
          ),
        ),
      );
      final origin = tester.getTopLeft(find.byType(DesignCanvas));
      final gesture = await tester.startGesture(
        origin + view.screen(Offset.zero),
      );
      await gesture.moveBy(const Offset(30, -20));
      await tester.pump();
      expect(editor.preview!.first, const Offset(3, 2));
      expect(model.calls, isEmpty);
      await gesture.up();
      await tester.pump();
      expect(model.calls.single['command'], 'design.point_edit');
      expect(model.calls.single['indices'], [0]);
      expect(model.calls.single['dx'], 3);
      expect(model.calls.single['dy'], 2);
      expect(tester.takeException(), isNull);
    },
  );

  testWidgets(
    'Escape cancels drag; point Delete/Cut never route to object deletion',
    (tester) async {
      tester.view.physicalSize = const Size(1440, 900);
      tester.view.devicePixelRatio = 1;
      addTearDown(tester.view.resetPhysicalSize);
      addTearDown(tester.view.resetDevicePixelRatio);
      final model = PointTestModel();
      addTearDown(model.dispose);
      await tester.pumpWidget(
        DesignApp(controller: model, startBackend: false),
      );
      await tester.pumpAndSettle();
      await tester.tap(find.byTooltip('Edit points').first);
      await tester.pumpAndSettle();
      final canvas = tester.widget<DesignCanvas>(find.byType(DesignCanvas));
      final editor = canvas.pointEditor!;
      expect(editor.active, isTrue);
      expect(find.text('Delete points'), findsOneWidget);
      canvas.view.scale = 10;
      canvas.view.translation = const Offset(60, 180);
      canvas.view.changed();
      await tester.pump();
      model.calls.clear();
      final origin = tester.getTopLeft(find.byType(DesignCanvas));
      final gesture = await tester.startGesture(
        origin + canvas.view.screen(Offset.zero),
      );
      await gesture.moveBy(const Offset(30, -20));
      await tester.pump();
      expect(editor.preview, isNotNull);
      await tester.sendKeyEvent(LogicalKeyboardKey.escape);
      await tester.pump();
      expect(editor.preview, isNull);
      expect(editor.active, isTrue);
      await gesture.up();
      await tester.pump();
      expect(model.calls, isEmpty);
      await tester.sendKeyDownEvent(LogicalKeyboardKey.controlLeft);
      await tester.sendKeyEvent(LogicalKeyboardKey.keyX);
      await tester.sendKeyUpEvent(LogicalKeyboardKey.controlLeft);
      expect(model.calls, isEmpty);
      await tester.sendKeyEvent(LogicalKeyboardKey.delete);
      await tester.pump();
      expect(model.calls.single['command'], 'design.point_edit');
      expect(model.calls.single['operation'], 'delete');
      model.calls.clear();
      editor.press(null, Offset.zero, Offset.zero);
      await tester.sendKeyEvent(LogicalKeyboardKey.delete);
      await tester.pump();
      expect(model.calls, isEmpty);
      await tester.sendKeyEvent(LogicalKeyboardKey.escape);
      await tester.pump();
      expect(editor.active, isFalse);
      expect(find.text('Delete points'), findsNothing);
      expect(tester.takeException(), isNull);
    },
  );

  test(
    'External revision cancels pending point preview and fixed-pixel hit testing works at zoom',
    () async {
      final model = PointTestModel();
      final editor = PointEditor(model);
      addTearDown(model.dispose);
      addTearDown(editor.dispose);
      editor.start();
      await editor.activate('contour');
      model.calls.clear();
      expect(editor.hit(const Offset(1007, 0), (p) => p * 100), 1);
      expect(editor.hit(const Offset(1011, 0), (p) => p * 100), isNull);
      editor.press(0, Offset.zero, Offset.zero);
      editor.motion(const Offset(20, 0), const Offset(2, 0), snap: false);
      model.externalChange();
      expect(editor.preview, isNull);
      expect(editor.source, isNull);
      await editor.release(inside: true);
      await editor.edit('delete');
      expect(model.calls, isEmpty);
    },
  );
}
