import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter/gestures.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:workbench_prototype/design/controller.dart';
import 'package:workbench_prototype/design/workspace.dart';
import 'package:workbench_prototype/design/canvas.dart';

class TestDesignController extends DesignController {
  final commands = <String>[];
  TestDesignController() {
    ready = true;
    busy = false;
    state = {
      'name': 'Design test',
      'units': 'in',
      'revision': 0,
      'entities': <Json>[],
      'selected': <String>[],
      'layers': <Json>[],
      'plates': <Json>[
        {
          'number': 1,
          'name': 'Plate 1',
          'x': 0,
          'y': 0,
          'width': 96,
          'height': 48,
        },
      ],
      'activePlate': 1,
      'dirty': false,
    };
  }
  @override
  Future<bool> run(String command, [Json params = const {}]) async {
    commands.add(command);
    return true;
  }

  @override
  Future<void> savePreferences(Json value) async {
    preferences = value;
  }
}

void main() {
  Future<TestDesignController> mount(
    WidgetTester tester, {
    Size size = const Size(1440, 900),
  }) async {
    tester.view.physicalSize = size;
    tester.view.devicePixelRatio = 1;
    addTearDown(tester.view.resetPhysicalSize);
    addTearDown(tester.view.resetDevicePixelRatio);
    final model = TestDesignController();
    addTearDown(model.dispose);
    await tester.pumpWidget(DesignApp(controller: model, startBackend: false));
    await tester.pumpAndSettle();
    await tester.pump(const Duration(milliseconds: 400));
    return model;
  }

  testWidgets(
    'Design renders with all six groups at desktop and compact sizes',
    (tester) async {
      await mount(tester);
      for (final group in designGroups.keys) {
        expect(find.byKey(ValueKey('group-$group')), findsOneWidget);
      }
      expect(find.byType(DesignCanvas), findsOneWidget);
      expect(tester.takeException(), isNull);
      tester.view.physicalSize = const Size(900, 650);
      await tester.pumpAndSettle();
      await tester.pump(const Duration(milliseconds: 400));
      expect(tester.takeException(), isNull);
    },
  );

  testWidgets('Toolbar groups reorder, float, move, dock, and persist layout', (
    tester,
  ) async {
    final model = await mount(tester);
    final grip = find.byKey(const ValueKey('grip-Create'));
    final first = tester.getCenter(find.byKey(const ValueKey('group-File')));
    await tester.dragFrom(
      tester.getCenter(grip),
      first - tester.getCenter(grip),
    );
    await tester.pumpAndSettle();
    await tester.pump(const Duration(milliseconds: 400));
    expect((model.preferences['order'] as List).first, 'Create');
    await tester.tap(find.byTooltip('Float Create palette'));
    await tester.pumpAndSettle();
    await tester.pump(const Duration(milliseconds: 400));
    expect(find.byKey(const ValueKey('group-Create')), findsNothing);
    expect(find.byKey(const ValueKey('dock-Create')), findsOneWidget);
    final dock = tester.getCenter(find.byKey(const ValueKey('dock-Create')));
    await tester.dragFrom(dock - const Offset(120, 0), const Offset(100, 80));
    await tester.pumpAndSettle();
    await tester.pump(const Duration(milliseconds: 400));
    expect(model.preferences['floating']['Create'], [190.0, 150.0]);
    await tester.tap(find.byKey(const ValueKey('dock-Create')));
    await tester.pumpAndSettle();
    await tester.pump(const Duration(milliseconds: 400));
    expect(find.byKey(const ValueKey('group-Create')), findsOneWidget);
    expect(model.preferences['floating'], isEmpty);
    expect(tester.takeException(), isNull);
  });

  testWidgets(
    'Drawing clicks dispatch geometry and shortcuts dispatch history',
    (tester) async {
      final model = await mount(tester);
      await tester.tap(find.byKey(const ValueKey('command-rectangle')));
      final canvas = tester.getRect(find.byType(DesignCanvas));
      await tester.tapAt(canvas.topLeft + const Offset(120, 120));
      await tester.pump();
      await tester.tapAt(canvas.topLeft + const Offset(260, 210));
      await tester.pumpAndSettle();
      await tester.pump(const Duration(milliseconds: 400));
      expect(model.commands, contains('geometry.add_rectangle'));
      await tester.sendKeyDownEvent(LogicalKeyboardKey.controlLeft);
      await tester.sendKeyEvent(LogicalKeyboardKey.keyZ);
      await tester.sendKeyUpEvent(LogicalKeyboardKey.controlLeft);
      await tester.pumpAndSettle();
      await tester.pump(const Duration(milliseconds: 400));
      expect(model.commands, contains('history.undo'));
      expect(tester.takeException(), isNull);
    },
  );

  testWidgets(
    'Bars stay left aligned and detached palettes drag back to an empty toolbar',
    (tester) async {
      final model = await mount(tester, size: const Size(1920, 1080));
      expect(tester.getRect(find.byKey(const ValueKey('menu-bar'))).left, 0);
      expect(
        tester.getRect(find.byKey(const ValueKey('menu-bar'))).width,
        1920,
      );
      expect(tester.getRect(find.byKey(const ValueKey('stock-bar'))).left, 0);
      expect(tester.getTopLeft(find.text('STOCK')).dx, 16);
      expect(tester.getTopLeft(find.text('File').first).dx, 16);
      for (final group in designGroups.keys) {
        await tester.tap(find.byTooltip('Float $group palette'));
        await tester.pumpAndSettle();
      }
      await tester.pump(const Duration(milliseconds: 400));
      expect(model.preferences['floating'].length, 6);
      final dock = find.byKey(const ValueKey('toolbar-dock'));
      expect(tester.getSize(dock).height, 24);
      expect(find.text('Drop a palette here to dock'), findsOneWidget);
      final grip = tester.getCenter(
        find.byKey(const ValueKey('palette-grip-Inspect')),
      );
      await tester.dragFrom(grip, tester.getCenter(dock) - grip);
      await tester.pumpAndSettle();
      await tester.pump(const Duration(milliseconds: 400));
      expect(find.byKey(const ValueKey('group-Inspect')), findsOneWidget);
      expect(model.preferences['floating'].containsKey('Inspect'), isFalse);
      expect(tester.getSize(dock).height, 58);
      // A floating palette can also insert before an existing docked group.
      final secondGrip = tester.getCenter(
        find.byKey(const ValueKey('palette-grip-Macros')),
      );
      final target = tester.getCenter(
        find.byKey(const ValueKey('group-Inspect')),
      );
      await tester.dragFrom(secondGrip, target - secondGrip);
      await tester.pumpAndSettle();
      await tester.pump(const Duration(milliseconds: 400));
      expect(find.byKey(const ValueKey('group-Macros')), findsOneWidget);
      expect(model.preferences['floating'].containsKey('Macros'), isFalse);
      expect(tester.takeException(), isNull);
    },
  );

  testWidgets(
    'Dialog typing stays local and drawing shortcuts resume on cancel',
    (tester) async {
      final model = await mount(tester);
      Future<void> chord(LogicalKeyboardKey key, {bool shift = false}) async {
        await tester.sendKeyDownEvent(LogicalKeyboardKey.controlLeft);
        if (shift) await tester.sendKeyDownEvent(LogicalKeyboardKey.shiftLeft);
        await tester.sendKeyEvent(key);
        if (shift) await tester.sendKeyUpEvent(LogicalKeyboardKey.shiftLeft);
        await tester.sendKeyUpEvent(LogicalKeyboardKey.controlLeft);
        await tester.pumpAndSettle();
      }

      await chord(LogicalKeyboardKey.keyN);
      expect(find.text('New drawing'), findsOneWidget);
      await tester.enterText(find.byType(TextField).first, 'Keep this text');
      model.commands.clear();
      await chord(LogicalKeyboardKey.keyA);
      await tester.sendKeyEvent(LogicalKeyboardKey.delete);
      await tester.pump();
      expect(model.commands, isEmpty);
      await tester.tap(find.text('Cancel'));
      await tester.pumpAndSettle();
      expect(
        FocusManager.instance.primaryFocus?.debugLabel,
        'Design shortcuts',
      );
      await chord(LogicalKeyboardKey.keyZ);
      await chord(LogicalKeyboardKey.keyZ, shift: true);
      expect(model.commands, ['history.undo', 'history.redo']);
      final nativeCalls = <String>[];
      tester.binding.defaultBinaryMessenger.setMockMethodCallHandler(
        const MethodChannel('workbench/design'),
        (call) async {
          nativeCalls.add(call.method);
          return null;
        },
      );
      addTearDown(
        () => tester.binding.defaultBinaryMessenger.setMockMethodCallHandler(
          const MethodChannel('workbench/design'),
          null,
        ),
      );
      await chord(LogicalKeyboardKey.keyS, shift: true);
      expect(nativeCalls, ['save']);
      await tester.tap(find.text('Edit').first);
      await tester.pumpAndSettle();
      expect(find.text('Ctrl+Y / Ctrl+Shift+Z'), findsOneWidget);
      expect(tester.takeException(), isNull);
    },
  );

  testWidgets(
    'Mouse drags group labels out, back, and sideways in a compact toolbar',
    (tester) async {
      final model = await mount(tester);
      expect(
        tester.getBottomRight(find.byKey(const ValueKey('stock-bar'))).dy,
        lessThanOrEqualTo(192),
      );
      Future<void> mouseDrag(Offset start, Offset end) async {
        final mouse = await tester.startGesture(
          start,
          kind: PointerDeviceKind.mouse,
        );
        await mouse.moveTo(start + (end - start) / 3);
        await tester.pump();
        await mouse.moveTo(end);
        await tester.pump();
        await mouse.up();
        await tester.pumpAndSettle();
        await tester.pump(const Duration(milliseconds: 400));
      }

      for (final group in designGroups.keys) {
        final label = find.byKey(ValueKey('group-label-$group'));
        await mouseDrag(tester.getCenter(label), const Offset(520, 360));
        expect(find.byKey(ValueKey('group-$group')), findsNothing);
        expect(model.preferences['floating'].containsKey(group), isTrue);
        await mouseDrag(
          tester.getCenter(find.byKey(ValueKey('palette-grip-$group'))),
          tester.getTopLeft(find.byKey(const ValueKey('toolbar-dock'))) +
              const Offset(1200, 20),
        );
        expect(find.byKey(ValueKey('group-$group')), findsOneWidget);
        expect(model.preferences['floating'].containsKey(group), isFalse);
      }
      await mouseDrag(
        tester.getCenter(find.byKey(const ValueKey('group-label-Modify'))),
        tester.getCenter(find.byKey(const ValueKey('group-File'))),
      );
      expect((model.preferences['order'] as List).first, 'Modify');
      expect(tester.takeException(), isNull);
    },
  );
}
