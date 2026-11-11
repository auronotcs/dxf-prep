import 'dart:io';
import 'dart:ui' as ui;
import 'package:flutter/gestures.dart';
import 'package:flutter/material.dart';
import 'package:flutter/rendering.dart';
import 'package:flutter/services.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:workbench_prototype/design/canvas.dart';
import 'package:workbench_prototype/design/workspace.dart';
import 'package:workbench_prototype/design/manufacturing_drawing.dart';
import 'package:workbench_prototype/design/manufacturing_workspace.dart';
import 'manufacturing_drawing_test.dart' show SelectionModel;
import 'document_tabs_test.dart' show TabModel;
import 'dimension_interaction_test.dart' show linear;

void main() {
  setUpAll(() async {
    for (final (name, path) in [
      ('Segoe UI', 'C:/Windows/Fonts/segoeui.ttf'),
      ('Roboto', 'C:/Windows/Fonts/segoeui.ttf'),
      (
        'MaterialIcons',
        '../.flutter-sdk/bin/cache/artifacts/material_fonts/MaterialIcons-Regular.otf',
      ),
    ]) {
      await (FontLoader(
        name,
      )..addFont(File(path).readAsBytes().then(ByteData.sublistView))).load();
    }
  });
  final camCanvas = find.byKey(const ValueKey('manufacturing-drawing-canvas'));
  dynamic host(WidgetTester t) => t.state(find.byType(DesignWorkspace));
  DesignViewport view(WidgetTester t) => host(t).view as DesignViewport;
  Future<void> switchTo(WidgetTester t, String name) async {
    await t.tap(find.byKey(ValueKey('workspace-$name')));
    await t.pumpAndSettle();
    await t.pump(const Duration(milliseconds: 400));
  }

  Future<SelectionModel> mount(WidgetTester t, {bool dark = false}) async {
    t.view.physicalSize = const Size(1920, 1080);
    t.view.devicePixelRatio = 1;
    addTearDown(t.view.resetPhysicalSize);
    addTearDown(t.view.resetDevicePixelRatio);
    final m = SelectionModel();
    addTearDown(m.dispose);
    await t.pumpWidget(
      RepaintBoundary(
        key: const ValueKey('unified-image'),
        child: DesignApp(controller: m, startBackend: false),
      ),
    );
    await t.pumpAndSettle();
    view(t).scale = 35;
    view(t).translation = const Offset(175, 550);
    view(t).grid = true;
    view(t).snap = true;
    view(t).changed();
    if (dark) {
      await t.tap(find.byTooltip('Toggle light / dark'));
    }
    await t.pumpAndSettle();
    return m;
  }

  Future<void> capture(WidgetTester t, String name) async {
    final boundary = t.renderObject<RenderRepaintBoundary>(
      find.byKey(const ValueKey('unified-image')),
    );
    await t.runAsync(() async {
      final image = await boundary.toImage();
      final bytes = await image.toByteData(format: ui.ImageByteFormat.png);
      await Directory('build/ui-review').create(recursive: true);
      await File(
        'build/ui-review/unified-$name.png',
      ).writeAsBytes(bytes!.buffer.asUint8List());
      image.dispose();
    });
  }

  testWidgets(
    'Shared chrome and exact canvas transform survive both switch directions',
    (t) async {
      final m = await mount(t);
      final original = view(t);
      final rect = t.getRect(find.byType(DesignCanvas));
      final stockRect = t.getRect(find.byKey(const ValueKey('stock-bar')));
      final selection = m.selection.toSet();
      await capture(t, 'design');
      await switchTo(t, 'manufacturing');
      expect(find.byType(Dialog), findsNothing);
      expect(find.text('workbench'), findsOneWidget);
      expect(find.byKey(const ValueKey('stock-bar')), findsOneWidget);
      expect(find.byKey(const ValueKey('history-bar')), findsOneWidget);
      expect(
        (t.state(find.byType(ManufacturingDrawing)) as dynamic).view,
        same(original),
      );
      expect(t.getRect(camCanvas), rect);
      expect(t.getRect(find.byKey(const ValueKey('stock-bar'))), stockRect);
      expect(original.scale, 35);
      expect(original.translation, const Offset(175, 550));
      expect(original.grid && original.snap, isTrue);
      expect(m.selection, selection);
      await capture(t, 'manufacturing');
      // Real pointer navigation in Manufacturing must be the returned Design view.
      await t.tap(find.byTooltip('Pan manufacturing drawing'));
      await t.dragFrom(t.getCenter(camCanvas), const Offset(45, -30));
      await t.sendEventToBinding(
        PointerScrollEvent(
          position: t.getCenter(camCanvas),
          scrollDelta: const Offset(0, -120),
        ),
      );
      await t.pumpAndSettle();
      final translation = original.translation, scale = original.scale;
      expect(scale, greaterThan(35));
      await t.tap(find.text('Grid'));
      await t.pumpAndSettle();
      expect(original.grid, isFalse);
      await switchTo(t, 'design');
      expect(t.getRect(find.byType(DesignCanvas)), rect);
      expect(view(t), same(original));
      expect(original.translation, translation);
      expect(original.scale, scale);
      expect(original.grid, isFalse);
      expect(original.snap, isTrue);
      expect(m.selection, selection);
      expect(m.calls.where((c) => c != 'manufacturing.cancel'), isEmpty);
      expect(t.takeException(), isNull);
    },
  );

  testWidgets(
    'Manufacturing selection returns to Design; hidden geometry tools cannot edit',
    (t) async {
      final m = await mount(t);
      m.state['selected'] = <String>[];
      m.notifyListeners();
      await t.pumpAndSettle();
      await switchTo(t, 'manufacturing');
      await t.tapAt(
        t.getTopLeft(camCanvas) + view(t).screen(const Offset(3, 3)),
      );
      await t.pumpAndSettle();
      expect(m.selection, {'entity_123456'});
      await t.sendKeyEvent(LogicalKeyboardKey.delete);
      await t.sendKeyEvent(LogicalKeyboardKey.arrowRight);
      await t.pumpAndSettle();
      expect(m.calls.any((c) => c.startsWith('geometry.')), isFalse);
      await switchTo(t, 'design');
      expect(m.selection, {'entity_123456'});
      expect(t.takeException(), isNull);
    },
  );

  testWidgets(
    'Machining job notices, history and generated review fit the shared inspector',
    (t) async {
      final m = await mount(t);
      m.state['readOnly'] = true;
      m.state['operationCount'] = 1;
      m.state['history'] = {
        'entries': ['Open', 'Add operation'],
        'index': 1,
      };
      m.state['canUndo'] = true;
      m.notifyListeners();
      await t.pumpAndSettle();
      final rect = t.getRect(find.byType(DesignCanvas));
      await switchTo(t, 'manufacturing');
      expect(t.getRect(camCanvas), rect);
      expect(
        t
            .widget<IconButton>(
              find.byWidgetPredicate(
                (w) => w is IconButton && w.tooltip == 'Undo',
              ),
            )
            .onPressed,
        isNotNull,
      );
      await t.tap(find.byKey(const ValueKey('command-cam.generate')));
      await t.pumpAndSettle();
      expect(m.calls, contains('manufacturing.generate'));
      expect(find.byType(MotionPreview), findsOneWidget);
      expect(find.text('Operations & review'), findsOneWidget);
      expect(t.takeException(), isNull);
      await capture(t, 'review');
      await t.tap(find.text('Drawing'));
      await t.pumpAndSettle();
      expect(t.getRect(camCanvas), rect);
      await switchTo(t, 'design');
      expect(t.getRect(find.byType(DesignCanvas)), rect);
    },
  );

  testWidgets(
    'Document tabs remain accessible and do not share view settings between drawings',
    (t) async {
      t.view.physicalSize = const Size(1440, 900);
      t.view.devicePixelRatio = 1;
      addTearDown(t.view.resetPhysicalSize);
      addTearDown(t.view.resetDevicePixelRatio);
      final a = TabModel(), b = TabModel();
      addTearDown(a.dispose);
      await t.pumpWidget(
        DesignApp(
          controller: a,
          startBackend: false,
          controllerFactory: () => b,
        ),
      );
      await t.pumpAndSettle();
      final first = host(t);
      final aView = view(t)..scale = 17;
      aView.translation = const Offset(31, 412);
      aView.grid = false;
      a.state['selected'] = ['a'];
      await switchTo(t, 'manufacturing');
      expect(find.byTooltip('New drawing tab'), findsOneWidget);
      final dynamic tabs = t.state(find.byType(DocumentTabs));
      final next =
          tabs.openDocument('project.new', {'name': 'Second'}) as Future<void>;
      await t.pumpAndSettle();
      await next;
      final bView = view(t)..scale = 9;
      bView.grid = true;
      await switchTo(t, 'manufacturing');
      tabs.setState(() {
        tabs.active = 0;
      });
      await t.pumpAndSettle();
      expect(host(t), same(first));
      expect(view(t), same(aView));
      expect(aView.scale, 17);
      expect(aView.translation, const Offset(31, 412));
      expect(aView.grid, isFalse);
      expect(a.selection, {'a'});
      await switchTo(t, 'design');
      expect(aView.scale, 17);
      tabs.setState(() {
        tabs.active = 1;
      });
      await t.pumpAndSettle();
      expect(view(t), same(bView));
      expect(bView.grid, isTrue);
      expect(bView.scale, 9);
      expect(t.takeException(), isNull);
    },
  );

  testWidgets(
    'Dimension selection and drawing guide visibility survive workspace changes',
    (t) async {
      final m = await mount(t);
      m.state['dimensions'] = [linear('horizontal')];
      m.notifyListeners();
      await t.pumpAndSettle();
      view(t).selectedDimensionId = 'd';
      view(t).guidesShown = false;
      view(t).changed();
      await t.pumpAndSettle();
      await switchTo(t, 'manufacturing');
      expect(view(t).selectedDimensionId, 'd');
      expect(view(t).guidesShown, isFalse);
      await switchTo(t, 'design');
      expect(view(t).selectedDimensionId, 'd');
      expect(view(t).guidesShown, isFalse);
      expect(t.takeException(), isNull);
    },
  );

  testWidgets('Both workspaces keep independent draggable palette layouts', (
    t,
  ) async {
    final m = await mount(t);
    final designOrder = List<String>.from(host(t).order);
    await switchTo(t, 'manufacturing');
    await t.tap(find.byTooltip('Float Setup palette'));
    await t.pumpAndSettle();
    await t.pump(const Duration(milliseconds: 400));
    expect(find.byKey(const ValueKey('palette-grip-Setup')), findsOneWidget);
    expect(m.preferences['manufacturingFloating'], contains('Setup'));
    expect(m.preferences['floating'], isEmpty);
    await switchTo(t, 'design');
    expect(host(t).order, designOrder);
    expect(host(t).floating, isEmpty);
    await switchTo(t, 'manufacturing');
    expect(find.byKey(const ValueKey('palette-grip-Setup')), findsOneWidget);
    final start = t.getCenter(find.byKey(const ValueKey('palette-grip-Setup')));
    final target = t.getCenter(find.byKey(const ValueKey('group-File')));
    await t.dragFrom(start, target - start);
    await t.pumpAndSettle();
    await t.pump(const Duration(milliseconds: 400));
    expect(host(t).floating, isEmpty);
    expect((host(t).order as List).first, 'Setup');
    expect(t.takeException(), isNull);
  });

  testWidgets(
    'Toolpath view retains navigation and repeated switches do not dispose the shared view',
    (t) async {
      await mount(t);
      final original = view(t);
      await switchTo(t, 'manufacturing');
      await t.tap(find.text('Toolpaths'));
      await t.pumpAndSettle();
      expect(find.text('Drawing'), findsOneWidget);
      await t.tap(find.text('Drawing'));
      await t.pumpAndSettle();
      expect(camCanvas, findsOneWidget);
      for (var i = 0; i < 3; i++) {
        await switchTo(t, 'design');
        original.zoom(1.1, const Offset(400, 300));
        final scale = original.scale, offset = original.translation;
        await switchTo(t, 'manufacturing');
        expect(original.scale, scale);
        expect(original.translation, offset);
      }
      await t.tap(find.byTooltip('Toggle light / dark'));
      await t.pumpAndSettle();
      await capture(t, 'dark');
      t.view.physicalSize = const Size(800, 600);
      await t.pumpAndSettle();
      expect(t.takeException(), isNull);
      await capture(t, 'compact');
      await switchTo(t, 'design');
      expect(t.takeException(), isNull);
    },
  );
}
