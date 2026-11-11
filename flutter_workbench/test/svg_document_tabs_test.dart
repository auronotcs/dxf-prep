import 'dart:convert';
import 'dart:io';
import 'dart:ui' as ui;
import 'package:flutter/material.dart';
import 'package:flutter/rendering.dart';
import 'package:flutter/services.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:workbench_prototype/design/controller.dart';
import 'package:workbench_prototype/design/workspace.dart';
import 'svg_import_test.dart' show SvgModel;

class SvgTabModel extends SvgModel {
  bool shut = false,
      disposed = false,
      throwStart = false,
      throwShutdown = false;
  final runs = <Json>[];
  @override
  Future<void> start() async {
    if (throwStart) throw StateError('Engine unavailable');
  }

  @override
  Future<void> shutdown() async {
    shut = true;
    if (throwShutdown) throw StateError('Pipe closed');
  }

  @override
  void dispose() {
    disposed = true;
    super.dispose();
  }

  @override
  Future<bool> run(String command, [Json params = const {}]) async {
    runs.add({'command': command, 'params': params});
    if (command == 'project.new') {
      commands.add(command);
      state['name'] = params['name'];
      state['units'] = params['units'];
      notifyListeners();
      return true;
    }
    if (command == 'recovery.clear') {
      commands.add(command);
      return true;
    }
    final ok = await super.run(command, params);
    if (ok && command == 'design.svg_apply') {
      state['entities'] = fixture['original']['entities'];
      state['bounds'] = fixture['original']['bounds'];
      state['dirty'] = true;
      state['canUndo'] = true;
      state['selected'] = <String>[];
      externalChange();
    }
    return ok;
  }
}

void main() {
  const ack = 'I reviewed dimensions, contours and omitted artwork notices';
  setUpAll(() async {
    for (final family in ['Segoe UI', 'Roboto']) {
      await (FontLoader(family)..addFont(
            File(
              'C:/Windows/Fonts/segoeui.ttf',
            ).readAsBytes().then(ByteData.sublistView),
          ))
          .load();
    }
    await (FontLoader('MaterialIcons')..addFont(
          File(
            '../.flutter-sdk/bin/cache/artifacts/material_fonts/MaterialIcons-Regular.otf',
          ).readAsBytes().then(ByteData.sublistView),
        ))
        .load();
  });
  Future<dynamic> mount(
    WidgetTester t,
    SvgTabModel source,
    SvgTabModel Function() factory, {
    Size size = const Size(1400, 900),
  }) async {
    t.view.physicalSize = size;
    t.view.devicePixelRatio = 1;
    addTearDown(t.view.resetPhysicalSize);
    addTearDown(t.view.resetDevicePixelRatio);
    addTearDown(source.dispose);
    await t.pumpWidget(
      RepaintBoundary(
        key: const ValueKey('svg-tabs-image'),
        child: DesignApp(
          controller: source,
          startBackend: false,
          controllerFactory: factory,
        ),
      ),
    );
    await t.pumpAndSettle();
    return t.state(find.byType(DocumentTabs));
  }

  Future<void> click(WidgetTester t, String label) async {
    final f = find.text(label);
    await t.ensureVisible(f);
    await t.pumpAndSettle();
    await t.tap(f);
    await t.pumpAndSettle();
  }

  void picker(WidgetTester t, String? path, List<String> calls) {
    t.binding.defaultBinaryMessenger.setMockMethodCallHandler(
      const MethodChannel('workbench/design'),
      (call) async {
        calls.add(call.method);
        return path;
      },
    );
    addTearDown(
      () => t.binding.defaultBinaryMessenger.setMockMethodCallHandler(
        const MethodChannel('workbench/design'),
        null,
      ),
    );
  }

  testWidgets(
    'Review publishes only its separate dirty tab and retains original state and view',
    (t) async {
      final source = SvgTabModel(), target = SvgTabModel();
      source.state.addAll({
        'name': 'Unsaved original',
        'units': 'mm',
        'dirty': true,
        'canUndo': true,
        'selected': ['original'],
        'readOnly': true,
      });
      final methods = <String>[];
      picker(t, 'C:/art/Mark.SVG', methods);
      final dynamic host = await mount(t, source, () => target);
      final dynamic workspace = t.state(find.byType(DesignWorkspace));
      workspace.view.scale = 17.0;
      workspace.view.translation = const Offset(111, 333);
      workspace.view.grid = true;
      workspace.view.snap = true;
      final before = jsonEncode(source.state);
      final action = workspace.action('openSvg') as Future<void>;
      await t.pumpAndSettle();
      expect(methods, ['svg']);
      expect(host.models.length, 1);
      expect(host.active, 0);
      expect(host.working, true);
      expect(find.text('Open SVG in new drawing'), findsOneWidget);
      expect(find.textContaining('physical size in mm'), findsOneWidget);
      expect(target.runs.single, {
        'command': 'project.new',
        'params': {'name': 'Mark', 'units': 'mm'},
      });
      await click(t, ack);
      await click(t, 'Open drawing tab');
      await action;
      expect(host.models.length, 2);
      expect(host.active, 1);
      expect(identical(host.models[1], target), true);
      expect(target.dirty, true);
      expect(target.state['canUndo'], true);
      expect(target.shut, false);
      expect(target.applied, {
        'token': target.fixture['original']['token'],
        'reviewed': true,
      });
      expect(jsonEncode(source.state), before);
      expect(source.commands, isEmpty);
      host.setState(() => host.active = 0);
      await t.pumpAndSettle();
      expect(identical(t.state(find.byType(DesignWorkspace)), workspace), true);
      expect(workspace.view.scale, 17);
      expect(workspace.view.translation, const Offset(111, 333));
      expect(workspace.view.grid && workspace.view.snap, true);
      expect(t.takeException(), isNull);
    },
  );
  testWidgets(
    'Compact private review cancel removes only the temporary session and recovery',
    (t) async {
      final source = SvgTabModel(), target = SvgTabModel();
      source.state['dirty'] = true;
      target.state['recovery'] = {'enabled': true};
      final dynamic host = await mount(
        t,
        source,
        () => target,
        size: const Size(800, 600),
      );
      final before = jsonEncode(source.state);
      final task =
          host.openDocument('svg.open', {
                'path': 'C:/art/Mark.svg',
                'units': 'in',
              })
              as Future<void>;
      await t.pumpAndSettle();
      expect(host.models.length, 1);
      await t.ensureVisible(find.byKey(const ValueKey('svg-preview')));
      await t.pumpAndSettle();
      final boundary = t.renderObject<RenderRepaintBoundary>(
        find.byKey(const ValueKey('svg-tabs-image')),
      );
      await t.runAsync(() async {
        final image = await boundary.toImage();
        final bytes = await image.toByteData(format: ui.ImageByteFormat.png);
        File(
          'build/ui-review/svg-new-document-compact.png',
        ).writeAsBytesSync(bytes!.buffer.asUint8List());
        image.dispose();
      });
      await click(t, 'Cancel');
      await task;
      expect(host.models.length, 1);
      expect(host.working, false);
      expect(target.shut && target.disposed, true);
      expect(target.commands, ['project.new', 'recovery.clear']);
      expect(source.commands, isEmpty);
      expect(jsonEncode(source.state), before);
      expect(t.takeException(), isNull);
    },
  );
  testWidgets(
    'Rejected source keeps review open and cancel recovers from shutdown error',
    (t) async {
      final source = SvgTabModel(),
          target = SvgTabModel()
            ..rejectRequests = true
            ..throwShutdown = true;
      final dynamic host = await mount(t, source, () => target);
      final task =
          host.openDocument('svg.open', {'path': 'C:/art/Mark.svg'})
              as Future<void>;
      await t.pumpAndSettle();
      expect(find.textContaining('source SVG changed'), findsOneWidget);
      expect(
        t
            .widget<FilledButton>(
              find.widgetWithText(FilledButton, 'Open drawing tab'),
            )
            .onPressed,
        isNull,
      );
      expect(host.models.length, 1);
      await click(t, 'Cancel');
      await task;
      expect(host.working, false);
      expect(target.disposed, true);
      expect(source.message, contains('did not shut down cleanly'));
      expect(source.commands, isEmpty);
      expect(t.takeException(), isNull);
    },
  );
  testWidgets(
    'Failed engine startup or unsupported backend never publishes a tab',
    (t) async {
      final source = SvgTabModel(),
          broken = SvgTabModel()..throwStart = true,
          old = SvgTabModel();
      old.state['svgImport'] = false;
      var count = 0;
      final dynamic host = await mount(
        t,
        source,
        () => count++ == 0 ? broken : old,
      );
      await host.openDocument('svg.open', {'path': 'C:/art/Mark.svg'});
      await t.pumpAndSettle();
      expect(host.models.length, 1);
      expect(host.working, false);
      expect(broken.shut && broken.disposed, true);
      expect(source.message, contains('Engine unavailable'));
      await host.openDocument('svg.open', {'path': 'C:/art/Mark.svg'});
      await t.pumpAndSettle();
      expect(old.shut && old.disposed, true);
      expect(source.message, contains('Restart the development launcher'));
      expect(host.working, false);
      expect(t.takeException(), isNull);
    },
  );
  testWidgets(
    'Picker cancel creates no session and current-drawing import remains separate',
    (t) async {
      final source = SvgTabModel();
      int count = 0;
      final methods = <String>[];
      picker(t, null, methods);
      final dynamic host = await mount(t, source, () {
        count++;
        return SvgTabModel();
      });
      final dynamic workspace = t.state(find.byType(DesignWorkspace));
      await workspace.action('openSvg');
      await t.pumpAndSettle();
      expect(methods, ['svg']);
      expect(count, 0);
      expect(host.models.length, 1);
      expect(source.commands, isEmpty);
      expect(
        designGroups['File']!.map((c) => c.id),
        containsAll(['openSvg', 'importSvg']),
      );
      expect(t.takeException(), isNull);
    },
  );
}
