import 'package:flutter/gestures.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:workbench_prototype/design/canvas.dart';
import 'package:workbench_prototype/design/footer.dart';
import 'package:workbench_prototype/design/workspace.dart';
import 'design_workspace_test.dart' show TestDesignController;

void main() {
  test('Measurements and zoom use geometry units, including open paths', () {
    expect(
      entityLength({
        'kind': 'line',
        'start': {'x': 0, 'y': 0},
        'end': {'x': 3, 'y': 4},
      }),
      5,
    );
    expect(
      entityLength({
        'kind': 'polyline',
        'points': [
          {'x': 0, 'y': 0},
          {'x': 3, 'y': 0},
          {'x': 3, 'y': 4},
        ],
        'closed': false,
      }),
      7,
    );
    expect(
      entityLength({
        'kind': 'polyline',
        'points': [
          {'x': 0, 'y': 0},
          {'x': 3, 'y': 0},
          {'x': 3, 'y': 4},
        ],
        'closed': true,
      }),
      12,
    );
    final view = DesignViewport()..scale = 96;
    expect(view.zoomPercent('in'), 100);
    view.scale = 96 / 25.4;
    expect(view.zoomPercent('mm'), 100);
    view.dispose();
  });

  testWidgets(
    'Full-width history and live status work at desktop and compact sizes',
    (tester) async {
      tester.view.physicalSize = const Size(1600, 900);
      tester.view.devicePixelRatio = 1;
      addTearDown(tester.view.resetPhysicalSize);
      addTearDown(tester.view.resetDevicePixelRatio);
      final model = TestDesignController();
      addTearDown(model.dispose);
      model.state.addAll({
        'canUndo': true,
        'canRedo': false,
        'history': {
          'index': 2,
          'entries': ['New drawing', 'Add rectangle', 'Move'],
        },
        'entities': [
          {
            'id': 'test-object-1',
            'kind': 'rectangle',
            'layer': '0',
            'corner_a': {'x': 0, 'y': 0},
            'corner_b': {'x': 10, 'y': 5},
            'bounds': [0, 0, 10, 5],
            'metadata': {'name': 'Panel', 'object_number': 7},
          },
          {
            'id': 'test-object-2',
            'kind': 'rectangle',
            'layer': '0',
            'corner_a': {'x': 0, 'y': 0},
            'corner_b': {'x': 10, 'y': 5},
            'bounds': [0, 0, 10, 5],
            'metadata': {'name': 'Top', 'object_number': 8},
          },
        ],
        'selected': ['test-object-1'],
        'selectionBounds': [0, 0, 10, 5],
      });
      await tester.pumpWidget(
        DesignApp(controller: model, startBackend: false),
      );
      await tester.pumpAndSettle();
      await tester.pump(const Duration(milliseconds: 400));
      expect(
        tester.getSize(find.byKey(const ValueKey('history-bar'))).width,
        1600,
      );
      expect(find.text('Size X 10.0000   Y 5.0000 in'), findsOneWidget);
      expect(find.text('Perimeter 30.0000 in'), findsOneWidget);
      await tester.tap(find.byTooltip('First history state'));
      await tester.pumpAndSettle();
      expect(model.commands, contains('history.goto'));
      final canvas = tester.widget<DesignCanvas>(find.byType(DesignCanvas));
      canvas.view.scale = 20;
      canvas.view.translation = const Offset(50, 200);
      canvas.view.changed();
      await tester.pump();
      final mouse = await tester.createGesture(kind: PointerDeviceKind.mouse);
      await mouse.addPointer(location: Offset.zero);
      final origin = tester.getTopLeft(find.byType(DesignCanvas));
      await mouse.moveTo(origin + const Offset(100, 150));
      await tester.pump();
      expect(find.text('X 2.5000   Y 2.5000 in'), findsOneWidget);
      expect(
        find.textContaining('Top (Object 8) · rectangle · 0 · +1 stacked'),
        findsOneWidget,
      );
      await mouse.moveTo(const Offset(5, 5));
      await tester.pump();
      expect(find.text('Object under pointer: --'), findsOneWidget);
      await tester.tap(find.byKey(const ValueKey('zoom-percentage')));
      await tester.pump();
      expect(find.text('Zoom 100.0%'), findsOneWidget);
      tester.view.physicalSize = const Size(900, 650);
      await tester.pumpAndSettle();
      expect(
        tester.getSize(find.byKey(const ValueKey('drawing-status-bar'))).height,
        48,
      );
      expect(
        tester.getTopLeft(find.byKey(const ValueKey('cursor-readout'))).dx,
        8,
      );
      expect(tester.takeException(), isNull);
      await mouse.removePointer();
    },
  );
}
