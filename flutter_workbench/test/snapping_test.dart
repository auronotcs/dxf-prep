import 'dart:io';
import 'package:flutter/gestures.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:workbench_prototype/design/controller.dart';
import 'package:workbench_prototype/design/canvas.dart';
import 'package:workbench_prototype/design/snapping.dart';
import 'package:workbench_prototype/design/point_editor.dart';
import 'point_editor_test.dart' show PointTestModel;

Json line(String id, Offset a, Offset b, {String layer = '0'}) => {
  'id': id,
  'kind': 'line',
  'layer': layer,
  'start': {'x': a.dx, 'y': a.dy},
  'end': {'x': b.dx, 'y': b.dy},
  'bounds': [
    a.dx < b.dx ? a.dx : b.dx,
    a.dy < b.dy ? a.dy : b.dy,
    a.dx > b.dx ? a.dx : b.dx,
    a.dy > b.dy ? a.dy : b.dy,
  ],
};

void main() {
  test(
    'Targets match Tkinter features, including closed seam and reversed corners',
    () {
      final index = SnapIndex([
        line('line', const Offset(-2, 1), const Offset(2, 1)),
        {
          'id': 'circle',
          'kind': 'circle',
          'center': {'x': 10, 'y': 10},
          'radius': 2,
        },
        {
          'id': 'rect',
          'kind': 'rectangle',
          'corner_a': {'x': 25, 'y': 25},
          'corner_b': {'x': 21, 'y': 21},
        },
        {
          'id': 'poly',
          'kind': 'polyline',
          'closed': true,
          'points': [
            {'x': 30, 'y': 0},
            {'x': 34, 'y': 0},
            {'x': 34, 'y': 4},
          ],
        },
      ]);
      for (final (p, label, id) in [
        (const Offset(-2, 1), 'Endpoint', 'line'),
        (const Offset(0, 1), 'Midpoint', 'line'),
        (const Offset(10, 10), 'Center', 'circle'),
        (const Offset(10, 12), 'Quadrant', 'circle'),
        (const Offset(8, 10), 'Quadrant', 'circle'),
        (const Offset(21, 25), 'Corner', 'rect'),
        (const Offset(23, 23), 'Center', 'rect'),
        (const Offset(34, 4), 'Vertex', 'poly'),
        (const Offset(32, 2), 'Midpoint', 'poly'),
      ]) {
        final found = index.nearest(p + const Offset(.01, .01), .1)!;
        expect(found.position, p);
        expect(found.label, label);
        expect(found.entityId, id);
      }
      expect(index.nearest(const Offset(32, 2), .1, exclude: {'poly'}), isNull);
    },
  );

  test(
    'Nearest wins, exact geometry ties beat grid, tolerance is pixels and no distant grid jump',
    () {
      final v = DesignViewport()
        ..snap = true
        ..scale = 100;
      addTearDown(v.dispose);
      v.snapIndex = SnapIndex([
        line('a', const Offset(1.031, 2.031), const Offset(3, 4)),
      ]);
      expect(
        v.snapWorld(const Offset(1.035, 2.035)),
        const Offset(1.031, 2.031),
      );
      expect(v.snapTarget?.label, 'Endpoint');
      expect(
        v.snapWorld(const Offset(1.035, 2.035), exclude: {'a'}),
        const Offset(1, 2),
      );
      v.snapIndex = SnapIndex([
        line('a', const Offset(1, 2), const Offset(3, 4)),
      ]);
      expect(v.snapWorld(const Offset(1, 2)), const Offset(1, 2));
      expect(v.snapTarget?.label, 'Endpoint');
      v.scale = 1000;
      expect(v.snapWorld(const Offset(1.04, 2.04)), const Offset(1.04, 2.04));
      expect(v.snapTarget, isNull);
      v.snap = false;
      expect(
        v.snapWorld(const Offset(1.001, 2.001)),
        const Offset(1.001, 2.001),
      );
      expect(v.snapTarget, isNull);
    },
  );

  test(
    'Physical grid spacing follows inch/mm units with negative coordinates',
    () {
      final v = DesignViewport()
        ..snap = true
        ..scale = 10;
      addTearDown(v.dispose);
      expect(
        v.snapWorld(const Offset(-1.13, 2.38)),
        const Offset(-1.125, 2.375),
      );
      v.snapGridStep = .125 * 25.4;
      v.scale = 10 / 25.4;
      final p = v.snapWorld(const Offset(-1.13, 2.38) * 25.4);
      expect(p.dx, closeTo(-1.125 * 25.4, 1e-9));
      expect(p.dy, closeTo(2.375 * 25.4, 1e-9));
    },
  );

  Future<void> mount(
    WidgetTester tester,
    PointTestModel m,
    DesignViewport v, {
    PointEditor? editor,
  }) async {
    await tester.pumpWidget(
      MaterialApp(
        home: Scaffold(
          body: DesignCanvas(
            model: m,
            view: v,
            layer: '0',
            dark: false,
            pointEditor: editor,
          ),
        ),
      ),
    );
    await tester.pump();
  }

  testWidgets(
    'Drawing uses visible locked targets, excludes hidden, and refreshes targets on revision',
    (tester) async {
      final m = PointTestModel(),
          v = DesignViewport()
            ..snap = true
            ..scale = 100
            ..translation = const Offset(100, 400)
            ..setTool('line');
      addTearDown(m.dispose);
      addTearDown(v.dispose);
      m.state['entities'] = [
        line(
          'locked',
          const Offset(1.031, 2.031),
          const Offset(3.031, 2.031),
          layer: 'locked',
        ),
        line(
          'hidden',
          const Offset(1.035, 2.035),
          const Offset(3.035, 2.035),
          layer: 'hidden',
        ),
      ];
      m.state['layers'] = [
        {'name': 'locked', 'locked': true, 'visible': true},
        {'name': 'hidden', 'visible': false},
      ];
      await mount(tester, m, v);
      final mouse = await tester.createGesture(kind: PointerDeviceKind.mouse);
      await mouse.addPointer(location: const Offset(204, 196));
      await mouse.moveTo(const Offset(203.5, 196.5));
      await tester.pump();
      expect(v.snapTarget?.entityId, 'locked');
      expect(v.snapTarget?.label, 'Endpoint');
      await tester.tapAt(const Offset(203.5, 196.5));
      await tester.pump();
      await tester.tapAt(const Offset(403.5, 196.5));
      await tester.pump();
      final call = m.calls.single;
      expect(call['command'], 'geometry.add_line');
      expect(call['x1'], 1.031);
      expect(call['y2'], 2.031);
      m.state['entities'] = [
        line('new', const Offset(1.04, 2.04), const Offset(3.04, 2.04)),
      ];
      m.externalChange();
      await mount(tester, m, v);
      await mouse.moveTo(const Offset(204, 196));
      await tester.pump();
      expect(v.snapTarget?.entityId, 'new');
      await mouse.removePointer();
      await tester.pump();
      expect(v.snapTarget, isNull);
      expect(tester.takeException(), isNull);
    },
  );

  testWidgets(
    'Object drag uses a source feature anchor, excludes its own group and commits final release',
    (tester) async {
      final m = PointTestModel(),
          v = DesignViewport()
            ..snap = true
            ..scale = 100
            ..translation = const Offset(100, 400);
      addTearDown(m.dispose);
      addTearDown(v.dispose);
      m.state['entities'] = [
        line('moving', const Offset(0, 0), const Offset(2, 0)),
        line('target', const Offset(3.031, 2.031), const Offset(5.031, 2.031)),
      ];
      m.state['selected'] = ['moving'];
      await mount(tester, m, v);
      final g = await tester.startGesture(const Offset(103, 399));
      await g.moveTo(const Offset(404.5, 196.5));
      await tester.pump();
      expect(v.snapTarget?.entityId, 'target');
      expect(v.dragDelta.dx, closeTo(3.031, 1e-9));
      expect(v.dragDelta.dy, closeTo(2.031, 1e-9));
      expect(m.calls, isEmpty);
      await g.up();
      await tester.pump();
      expect(m.calls.single['command'], 'geometry.transform');
      expect(m.calls.single['dx'], closeTo(3.031, 1e-9));
      expect(v.snapTarget, isNull);
    },
  );

  testWidgets('Cancelled, stale or outside object drags do not commit', (
    tester,
  ) async {
    final m = PointTestModel(),
        v = DesignViewport()
          ..snap = true
          ..scale = 10
          ..translation = const Offset(100, 300);
    addTearDown(m.dispose);
    addTearDown(v.dispose);
    for (final mode in ['cancel', 'escape', 'revision', 'outside']) {
      await mount(tester, m, v);
      final g = await tester.startGesture(const Offset(101, 299));
      await g.moveTo(const Offset(150, 250));
      await tester.pump();
      if (mode == 'cancel') {
        await g.cancel();
      } else {
        if (mode == 'escape') v.setTool('select');
        if (mode == 'revision') m.externalChange();
        if (mode == 'outside') await g.moveTo(const Offset(-20, -20));
        await g.up();
      }
      await tester.pump();
      expect(m.calls, isEmpty, reason: mode);
      expect(v.dragDelta, Offset.zero);
    }
  });

  testWidgets(
    'Point editing snaps to another object and excludes the entire edited contour',
    (tester) async {
      final m = PointTestModel(),
          v = DesignViewport()
            ..snap = true
            ..scale = 100
            ..translation = const Offset(100, 400);
      final editor = PointEditor(m);
      addTearDown(m.dispose);
      addTearDown(v.dispose);
      addTearDown(editor.dispose);
      m.state['entities'].add(
        line('target', const Offset(2.031, 1.031), const Offset(4.031, 1.031)),
      );
      editor.start();
      await editor.activate('contour');
      m.calls.clear();
      await mount(tester, m, v, editor: editor);
      final g = await tester.startGesture(const Offset(100, 400));
      await g.moveTo(const Offset(303.5, 296.5));
      await tester.pump();
      expect(v.snapTarget?.entityId, 'target');
      expect(editor.preview!.first, const Offset(2.031, 1.031));
      expect(m.calls, isEmpty);
      await g.up();
      await tester.pump();
      expect(m.calls.single['command'], 'design.point_edit');
      expect(m.calls.single['dx'], 2.031);
      expect(m.calls.single['dy'], 1.031);
      expect(tester.takeException(), isNull);
    },
  );

  test(
    'Real backend retains snapped endpoints through Undo/Redo and CAM save/reopen',
    () async {
      final m = DesignController();
      final temp = Directory.systemTemp.createTempSync('flutter-snap-');
      try {
        await m.start();
        expect(m.ready, true, reason: m.message);
        await m.run('geometry.add_line', {
          'x1': 1.031,
          'y1': 2.031,
          'x2': 3.031,
          'y2': 2.031,
        });
        final v = DesignViewport()
          ..scale = 100
          ..snap = true
          ..snapIndex = SnapIndex(m.entities.where(m.visible));
        final a = v.snapWorld(const Offset(1.035, 2.035));
        final b = v.snapWorld(const Offset(3.035, 2.035));
        await m.run('geometry.add_line', {
          'x1': a.dx,
          'y1': a.dy,
          'x2': b.dx,
          'y2': b.dy,
        });
        final id = m.entities.last['id'];
        expect(m.entities.last['start'], {'x': 1.031, 'y': 2.031});
        await m.run('history.undo');
        expect(m.entities, hasLength(1));
        await m.run('history.redo');
        expect(m.entities.last['id'], id);
        final path = '${temp.path}/snapped.camjob.json';
        expect(await m.run('project.save', {'path': path}), true);
        expect(await m.run('project.open', {'path': path}), true);
        expect(m.entities.last['end'], {'x': 3.031, 'y': 2.031});
        expect(m.entities.last['id'], id);
        v.dispose();
      } finally {
        await m.shutdown();
        m.dispose();
        temp.deleteSync(recursive: true);
      }
    },
  );
}
