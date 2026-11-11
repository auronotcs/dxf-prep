import 'dart:async';
import 'dart:convert';
import 'dart:io';
import 'dart:ui' as ui;
import 'package:flutter/material.dart';
import 'package:flutter/rendering.dart';
import 'package:flutter/services.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:workbench_prototype/design/controller.dart';
import 'package:workbench_prototype/design/svg_import_dialog.dart';
import 'package:workbench_prototype/design/workspace.dart';
import 'design_workspace_test.dart' show TestDesignController;

class SvgModel extends TestDesignController {
  final Json fixture = jsonDecode(
    File('test/fixtures/svg_import_review.json').readAsStringSync(),
  );
  final requests = <Json>[];
  Json? applied;
  bool rejectRequests = false;
  SvgModel() {
    state['svgImport'] = true;
  }
  @override
  Future<Json> request(String command, [Json params = const {}]) async {
    requests.add({'command': command, 'params': params});
    if (command == 'design.svg_begin') {
      return {'ok': true, 'context': fixture['context']};
    }
    if (rejectRequests) {
      return {
        'ok': false,
        'error':
            'The source SVG changed. Reopen the import to review its new contents.',
      };
    }
    return {'ok': true, 'preview': fixture[params['placement']]};
  }

  @override
  Future<bool> run(String command, [Json params = const {}]) async {
    commands.add(command);
    applied = params;
    if (rejectRequests) {
      message =
          'The source SVG changed. Reopen the import to review its new contents.';
      return false;
    }
    return true;
  }

  void externalChange() {
    state['revision'] = (state['revision'] as int) + 1;
    notifyListeners();
  }
}

void main() {
  setUpAll(() async {
    await (FontLoader('MaterialIcons')..addFont(
          File(
            '../.flutter-sdk/bin/cache/artifacts/material_fonts/MaterialIcons-Regular.otf',
          ).readAsBytes().then(ByteData.sublistView),
        ))
        .load();
    await (FontLoader('Roboto')..addFont(
          File(
            'C:/Windows/Fonts/segoeui.ttf',
          ).readAsBytes().then(ByteData.sublistView),
        ))
        .load();
  });
  Future<void> setupSize(
    WidgetTester t, [
    Size size = const Size(800, 600),
  ]) async {
    t.view.physicalSize = size;
    t.view.devicePixelRatio = 1;
    addTearDown(t.view.resetPhysicalSize);
    addTearDown(t.view.resetDevicePixelRatio);
  }

  Future<void> mount(WidgetTester t, SvgModel m) async {
    await setupSize(t);
    addTearDown(m.dispose);
    await t.pumpWidget(
      RepaintBoundary(
        key: const ValueKey('svg-image'),
        child: MaterialApp(
          home: Builder(
            builder: (context) => Scaffold(
              body: TextButton(
                onPressed: () => showDialog<bool>(
                  context: context,
                  builder: (_) =>
                      SvgImportDialog(model: m, path: 'C:/inputs/art.svg'),
                ),
                child: const Text('Start import'),
              ),
            ),
          ),
        ),
      ),
    );
    await t.tap(find.text('Start import'));
    await t.pumpAndSettle();
  }

  Future<void> click(WidgetTester t, String label) async {
    final f = find.text(label);
    await t.ensureVisible(f);
    await t.pumpAndSettle();
    await t.tap(f);
    await t.pumpAndSettle();
  }

  Future<void> enter(WidgetTester t, String label, String value) async {
    final f = find.byWidgetPredicate(
      (w) => w is TextField && w.decoration?.labelText == label,
    );
    await t.ensureVisible(f);
    await t.enterText(f, value);
    await t.pumpAndSettle();
  }

  bool canApply(WidgetTester t) =>
      t
          .widget<FilledButton>(
            find.widgetWithText(FilledButton, 'Import geometry'),
          )
          .onPressed !=
      null;

  testWidgets(
    'Private physical SVG review displays omissions, placement and fresh acknowledgment',
    (t) async {
      final m = SvgModel();
      final before = jsonEncode(m.state);
      await mount(t, m);
      expect(find.byKey(const ValueKey('svg-preview')), findsOneWidget);
      expect(canApply(t), false);
      expect(find.textContaining('2 closed / 2 open contours'), findsOneWidget);
      expect(
        find.textContaining('SVG page: 4.0000 × 3.0000 in'),
        findsOneWidget,
      );
      expect(find.textContaining('text: 1'), findsOneWidget);
      expect(find.textContaining('centerline'), findsOneWidget);
      await click(
        t,
        'I reviewed dimensions, contours and omitted artwork notices',
      );
      expect(canApply(t), true);
      await click(t, 'Keep SVG page coordinates');
      await click(t, 'Specify geometry lower-left');
      expect(find.byKey(const ValueKey('svg-preview')), findsNothing);
      expect(canApply(t), false);
      await enter(t, 'Lower-left X', '12');
      await enter(t, 'Lower-left Y', '8');
      await click(t, 'Preview placement');
      expect(m.requests.last['params']['x'], 12);
      expect(m.requests.last['params']['y'], 8);
      expect(
        find.textContaining('Geometry lower-left: 12.0000, 8.0000 in'),
        findsOneWidget,
      );
      await t.ensureVisible(find.byKey(const ValueKey('svg-preview')));
      await t.pumpAndSettle();
      final boundary = t.renderObject<RenderRepaintBoundary>(
        find.byKey(const ValueKey('svg-image')),
      );
      await t.runAsync(() async {
        final image = await boundary.toImage();
        final bytes = await image.toByteData(format: ui.ImageByteFormat.png);
        File(
          'build/ui-review/svg-import-compact.png',
        ).writeAsBytesSync(bytes!.buffer.asUint8List());
        image.dispose();
      });
      await click(t, 'Cancel');
      expect(find.byType(SvgImportDialog), findsNothing);
      expect(jsonEncode(m.state), before);
      expect(m.commands, isEmpty);
      expect(t.takeException(), isNull);
    },
  );

  testWidgets(
    'Only reviewed current preview applies; parse and document changes disable it',
    (t) async {
      final m = SvgModel();
      await mount(t, m);
      await click(
        t,
        'I reviewed dimensions, contours and omitted artwork notices',
      );
      await enter(t, 'Curve tolerance', 'NaN');
      final count = m.requests.length;
      await click(t, 'Preview placement');
      expect(m.requests.length, count);
      expect(canApply(t), false);
      await enter(t, 'Curve tolerance', '.001');
      await click(t, 'Preview placement');
      await click(
        t,
        'I reviewed dimensions, contours and omitted artwork notices',
      );
      m.rejectRequests = true;
      await click(t, 'Import geometry');
      expect(find.textContaining('source SVG changed'), findsOneWidget);
      expect(canApply(t), false);
      m.rejectRequests = false;
      await click(t, 'Preview placement');
      m.externalChange();
      await t.pumpAndSettle();
      expect(canApply(t), false);
      expect(
        find.text('Drawing changed. Close and reopen SVG import.'),
        findsOneWidget,
      );
      expect(t.takeException(), isNull);
    },
  );

  testWidgets(
    'Apply sends exactly the reviewed preview token and closes the private dialog',
    (t) async {
      final m = SvgModel();
      await mount(t, m);
      await click(
        t,
        'I reviewed dimensions, contours and omitted artwork notices',
      );
      await click(t, 'Import geometry');
      expect(m.commands, ['design.svg_apply']);
      expect(m.applied, {
        'token': m.fixture['original']['token'],
        'reviewed': true,
      });
      expect(find.byType(SvgImportDialog), findsNothing);
      expect(t.takeException(), isNull);
    },
  );

  testWidgets(
    'Dedicated native SVG picker and cancellation retain the workspace camera',
    (t) async {
      await setupSize(t, const Size(1400, 900));
      final m = SvgModel();
      addTearDown(m.dispose);
      final methods = <String>[];
      t.binding.defaultBinaryMessenger.setMockMethodCallHandler(
        const MethodChannel('workbench/design'),
        (call) async {
          methods.add(call.method);
          return 'C:/inputs/art.svg';
        },
      );
      addTearDown(
        () => t.binding.defaultBinaryMessenger.setMockMethodCallHandler(
          const MethodChannel('workbench/design'),
          null,
        ),
      );
      await t.pumpWidget(DesignApp(controller: m, startBackend: false));
      await t.pumpAndSettle();
      final dynamic w = t.state(find.byType(DesignWorkspace));
      w.view.scale = 32.0;
      w.view.translation = const Offset(120, 450);
      w.view.grid = true;
      final task = w.action('importSvg') as Future<void>;
      await t.pumpAndSettle();
      expect(methods, ['svg']);
      expect(find.byType(SvgImportDialog), findsOneWidget);
      await click(t, 'Cancel');
      await task;
      expect(w.view.scale, 32);
      expect(w.view.translation, const Offset(120, 450));
      expect(w.view.grid, true);
      expect(m.commands, isEmpty);
      final pending = Completer<String?>();
      t.binding.defaultBinaryMessenger.setMockMethodCallHandler(
        const MethodChannel('workbench/design'),
        (_) => pending.future,
      );
      final changed = w.action('importSvg') as Future<void>;
      await t.pump();
      m.externalChange();
      pending.complete('C:/inputs/art.svg');
      await t.pumpAndSettle();
      await changed;
      expect(find.byType(SvgImportDialog), findsNothing);
      expect(t.takeException(), isNull);
    },
  );
}
