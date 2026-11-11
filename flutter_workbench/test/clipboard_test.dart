import 'package:flutter/gestures.dart';
import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:workbench_prototype/design/controller.dart';
import 'package:workbench_prototype/design/workspace.dart';
import 'package:workbench_prototype/design/canvas.dart';
import 'design_workspace_test.dart' show TestDesignController;

class ClipboardController extends TestDesignController {
  final calls = <(String, Json)>[];
  @override
  Future<bool> run(String command, [Json params = const {}]) async {
    calls.add((command, params));
    return true;
  }
}

void main() {
  Future<void> mount(WidgetTester tester, ClipboardController model) async {
    tester.view.physicalSize = const Size(1440, 900);
    tester.view.devicePixelRatio = 1;
    addTearDown(tester.view.resetPhysicalSize);
    addTearDown(tester.view.resetDevicePixelRatio);
    addTearDown(model.dispose);
    await tester.pumpWidget(DesignApp(controller: model, startBackend: false));
    await tester.pumpAndSettle();
  }

  Future<void> pasteKey(WidgetTester tester) async {
    await tester.sendKeyDownEvent(LogicalKeyboardKey.controlLeft);
    await tester.sendKeyEvent(LogicalKeyboardKey.keyV);
    await tester.sendKeyUpEvent(LogicalKeyboardKey.controlLeft);
    await tester.pumpAndSettle();
  }

  testWidgets('Empty clipboard disables both paste menus and Ctrl+V', (
    tester,
  ) async {
    final model = ClipboardController();
    await mount(tester, model);
    await pasteKey(tester);
    expect(model.calls, isEmpty);
    await tester.tap(find.byTooltip('Edit menu'));
    await tester.pumpAndSettle();
    for (final label in ['Paste', 'Paste to Current Layer']) {
      final item = find.ancestor(
        of: find.text(label),
        matching: find.byType(PopupMenuItem<String>),
      );
      expect(tester.widget<PopupMenuItem<String>>(item).enabled, false);
    }
    expect(tester.takeException(), isNull);
  });

  testWidgets('Ctrl+V pastes in place and Edit pastes to the Draw on layer', (
    tester,
  ) async {
    final model = ClipboardController();
    model.state['clipboardCount'] = 2;
    model.state['layers'] = <Json>[
      {'name': '0', 'visible': true, 'locked': false},
      {'name': 'Cutouts', 'visible': true, 'locked': false},
    ];
    await mount(tester, model);
    await pasteKey(tester);
    expect(model.calls.single.$1, 'design.paste');
    expect(model.calls.single.$2, isEmpty);
    final dynamic workspace = tester.state(find.byType(DesignWorkspace));
    workspace.setState(() {
      workspace.layer = 'Cutouts';
    });
    await tester.pump();
    await tester.tap(find.byTooltip('Edit menu'));
    await tester.pumpAndSettle();
    await tester.tap(find.text('Paste to Current Layer'));
    await tester.pumpAndSettle();
    expect(model.calls.last.$1, 'design.paste');
    expect(model.calls.last.$2, {'target_layer': 'Cutouts'});
    expect(tester.takeException(), isNull);
  });

  testWidgets(
    'Canvas context paste works without selection and respects view-only jobs',
    (tester) async {
      final model = ClipboardController();
      model.state['clipboardCount'] = 1;
      await mount(tester, model);
      final at = tester.getCenter(find.byType(DesignCanvas));
      await tester.tapAt(at, buttons: kSecondaryMouseButton);
      await tester.pumpAndSettle();
      expect(find.text('Paste to Current Layer'), findsOneWidget);
      await tester.tap(find.text('Paste'));
      await tester.pumpAndSettle();
      expect(model.calls.single.$1, 'design.paste');
      model.state['readOnly'] = true;
      model.notifyListeners();
      await tester.pump();
      await pasteKey(tester);
      expect(model.calls, hasLength(1));
      await tester.tapAt(at, buttons: kSecondaryMouseButton);
      await tester.pumpAndSettle();
      final item = find.ancestor(
        of: find.text('Paste to Current Layer'),
        matching: find.byType(PopupMenuItem<String>),
      );
      expect(tester.widget<PopupMenuItem<String>>(item).enabled, false);
      expect(tester.takeException(), isNull);
    },
  );
}
