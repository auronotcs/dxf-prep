import 'dart:convert';
import 'dart:io';
import 'dart:ui' as ui;
import 'package:flutter/material.dart';
import 'package:flutter/rendering.dart';
import 'package:flutter/services.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:workbench_prototype/design/controller.dart';
import 'package:workbench_prototype/design/operation_recipe_dialog.dart';
import 'package:workbench_prototype/design/workspace.dart';
import 'package:workbench_prototype/design/manufacturing_workspace.dart';
import 'design_workspace_test.dart' show TestDesignController;

class RecipeModel extends TestDesignController {
  final Json fixture;
  final requests = <Json>[];
  final edits = <Json>[];
  @override
  Future<bool> run(String command, [Json params = const {}]) async {
    edits.add({'command': command, 'params': params});
    return super.run(command, params);
  }

  bool reject = false;
  int refreshes = 0;
  Json? returned;
  RecipeModel({String fixtureName = 'operation_recipe'})
    : fixture = jsonDecode(
        File('test/fixtures/$fixtureName.json').readAsStringSync(),
      ) {
    state = jsonDecode(jsonEncode(fixture['state']));
  }
  @override
  Future<Json> request(String command, [Json params = const {}]) async {
    requests.add({'command': command, 'params': params});
    if (command == 'manufacturing.attachment_list') {
      final Json r = jsonDecode(jsonEncode(fixture['listing']));
      r['token'] = 'choices-${++refreshes}';
      return {'ok': true, 'review': r};
    }
    if (command == 'manufacturing.attachment_review') {
      if (reject) {
        return {
          'ok': false,
          'error': 'Library changed. Refresh recipe choices.',
        };
      }
      return {'ok': true, 'review': fixture['draft']};
    }
    if (command == 'manufacturing.guided_readiness') {
      return {'ok': true, 'review': fixture['readiness']};
    }
    if (command == 'manufacturing.guided_action') {
      return {
        'ok': true,
        'review': {
          'action': params['action'],
          'operation_id': params['operation_id'],
        },
      };
    }
    if (command == 'manufacturing.attachment_prepare') {
      return {'ok': true, 'manufacturing': fixture['motion']};
    }
    return {
      'ok': true,
      'manufacturing': {'status': 'canceled'},
    };
  }

  void change() {
    state['revision'] = (state['revision'] as int) + 1;
    notifyListeners();
  }
}

void main() {
  setUpAll(() async {
    for (final family in ['Roboto', 'Segoe UI']) {
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
  Future<void> size(WidgetTester t, [Size s = const Size(800, 600)]) async {
    t.view.physicalSize = s;
    t.view.devicePixelRatio = 1;
    addTearDown(t.view.resetPhysicalSize);
    addTearDown(t.view.resetDevicePixelRatio);
  }

  Future<void> click(WidgetTester t, String text) async {
    final f = find.text(text);
    await t.ensureVisible(f);
    await t.pumpAndSettle();
    await t.tap(f);
    await t.pumpAndSettle();
  }

  bool enabled(WidgetTester t) =>
      t
          .widget<FilledButton>(
            find.widgetWithText(FilledButton, 'Generate motion review'),
          )
          .onPressed !=
      null;
  Future<void> mount(WidgetTester t, RecipeModel m) async {
    await size(t);
    addTearDown(m.dispose);
    await t.pumpWidget(
      RepaintBoundary(
        key: const ValueKey('recipe-image'),
        child: MaterialApp(
          home: Builder(
            builder: (context) => Scaffold(
              body: TextButton(
                onPressed: () async {
                  m.returned = await showDialog<Json>(
                    context: context,
                    builder: (_) => OperationRecipeDialog(
                      model: m,
                      operationId: m.fixture['listing']['operation_id'],
                    ),
                  );
                },
                child: const Text('Start'),
              ),
            ),
          ),
        ),
      ),
    );
    await click(t, 'Start');
  }

  testWidgets(
    'Cleanout recipes review passes spacing and links before motion',
    (t) async {
      final m = RecipeModel(fixtureName: 'fill_recipe_preset');
      final before = jsonEncode(m.state);
      await mount(t, m);
      await click(t, 'Review settings');
      expect(find.textContaining('Stepover (in):'), findsOneWidget);
      expect(find.textContaining('Pass schedule:'), findsOneWidget);
      expect(
        find.textContaining('Floor finishing feed (in/s):'),
        findsOneWidget,
      );
      expect(find.textContaining('Keep tool down:'), findsOneWidget);
      expect(find.textContaining('Cover cleanout corners:'), findsOneWidget);
      await click(t, 'I reviewed recipe scope and the exact setting changes.');
      expect(enabled(t), false);
      await click(t, 'I reviewed the changes to depth, tabs and hold-down.');
      expect(enabled(t), true);
      final boundary = t.renderObject<RenderRepaintBoundary>(
        find.byKey(const ValueKey('recipe-image')),
      );
      await t.runAsync(() async {
        final image = await boundary.toImage();
        final bytes = await image.toByteData(format: ui.ImageByteFormat.png);
        File(
          'build/ui-review/fill-recipe-preset.png',
        ).writeAsBytesSync(bytes!.buffer.asUint8List());
        image.dispose();
      });
      await click(t, 'Generate motion review');
      expect(m.returned!['holding_reviewed'], true);
      expect(jsonEncode(m.state), before);
      expect(m.commands, isEmpty);
      expect(t.takeException(), isNull);
    },
  );
  testWidgets('Cut presets require both settings and holding review', (
    t,
  ) async {
    final m = RecipeModel(fixtureName: 'router_recipe_preset');
    final before = jsonEncode(m.state);
    await mount(t, m);
    await click(t, 'Review settings');
    expect(find.textContaining('entry_mode: plunge'), findsOneWidget);
    expect(find.textContaining('target_depth: 0.5'), findsOneWidget);
    expect(find.textContaining('pass_depths:'), findsOneWidget);
    await click(t, 'I reviewed recipe scope and the exact setting changes.');
    expect(enabled(t), false);
    await click(t, 'I reviewed the changes to depth, tabs and hold-down.');
    expect(enabled(t), true);
    final boundary = t.renderObject<RenderRepaintBoundary>(
      find.byKey(const ValueKey('recipe-image')),
    );
    await t.runAsync(() async {
      final image = await boundary.toImage();
      final bytes = await image.toByteData(format: ui.ImageByteFormat.png);
      File(
        'build/ui-review/router-recipe-preset.png',
      ).writeAsBytesSync(bytes!.buffer.asUint8List());
      image.dispose();
    });
    await click(t, 'Generate motion review');
    expect(m.returned!['holding_reviewed'], true);
    expect(m.returned!['reviewed'], true);
    expect(jsonEncode(m.state), before);
    expect(m.commands, isEmpty);
    expect(t.takeException(), isNull);
  });
  testWidgets(
    'Preset holding acknowledgment resets after refresh and job edits',
    (t) async {
      final m = RecipeModel(fixtureName: 'router_recipe_preset');
      await mount(t, m);
      for (final mode in ['refresh', 'edit']) {
        await click(t, 'Review settings');
        await click(
          t,
          'I reviewed recipe scope and the exact setting changes.',
        );
        await click(t, 'I reviewed the changes to depth, tabs and hold-down.');
        expect(enabled(t), true);
        if (mode == 'edit') {
          m.change();
          await t.pumpAndSettle();
          expect(enabled(t), false);
        }
        await click(t, 'Refresh choices');
        await click(t, 'Review settings');
        await click(
          t,
          'I reviewed recipe scope and the exact setting changes.',
        );
        expect(enabled(t), false);
      }
      await click(t, 'Cancel');
      expect(m.returned, isNull);
      expect(t.takeException(), isNull);
    },
  );
  testWidgets(
    'Compact choice shows scope and excludes retired recipes without mutation',
    (t) async {
      final m = RecipeModel();
      final before = jsonEncode(m.state);
      await mount(t, m);
      expect(enabled(t), false);
      expect(find.textContaining('Synthetic fixture only'), findsWidgets);
      await click(t, 'Show excluded recipes');
      expect(find.textContaining('Excluded'), findsWidgets);
      final buttons = t
          .widgetList<OutlinedButton>(
            find.widgetWithText(OutlinedButton, 'Review settings'),
          )
          .toList();
      expect(buttons.where((b) => b.onPressed != null).length, 1);
      await click(t, 'Show excluded recipes');
      final boundary = t.renderObject<RenderRepaintBoundary>(
        find.byKey(const ValueKey('recipe-image')),
      );
      await t.runAsync(() async {
        final image = await boundary.toImage();
        final bytes = await image.toByteData(format: ui.ImageByteFormat.png);
        File(
          'build/ui-review/operation-recipe-compact.png',
        ).writeAsBytesSync(bytes!.buffer.asUint8List());
        image.dispose();
      });
      await click(t, 'Cancel');
      expect(m.returned, isNull);
      expect(jsonEncode(m.state), before);
      expect(m.commands, isEmpty);
      expect(t.takeException(), isNull);
    },
  );
  testWidgets(
    'Before-after review requires explicit acknowledgment and returns exact current choice',
    (t) async {
      final m = RecipeModel();
      await mount(t, m);
      await click(t, 'Review settings');
      expect(find.textContaining('feed_rate: 3'), findsOneWidget);
      expect(find.textContaining('target_depth:'), findsOneWidget);
      expect(enabled(t), false);
      await click(t, 'I reviewed recipe scope and the exact setting changes.');
      expect(enabled(t), true);
      await click(t, 'Generate motion review');
      expect(m.returned, {
        'choice': {
          'operation_id': m.fixture['listing']['operation_id'],
          'token': 'choices-1',
          'id': 'custom-guided',
          'recipe': 0,
        },
        'reviewed': true,
      });
      expect(m.commands, isEmpty);
      expect(t.takeException(), isNull);
    },
  );
  testWidgets('Job edits discard acknowledgment and refresh binds new choice', (
    t,
  ) async {
    final m = RecipeModel();
    await mount(t, m);
    await click(t, 'Review settings');
    await click(t, 'I reviewed recipe scope and the exact setting changes.');
    m.change();
    await t.pumpAndSettle();
    expect(enabled(t), false);
    await click(t, 'Refresh choices');
    await click(t, 'Review settings');
    expect(m.requests.last['params']['token'], 'choices-2');
    expect(enabled(t), false);
    expect(t.takeException(), isNull);
  });
  testWidgets('Changed library clears old choice and requires refresh', (
    t,
  ) async {
    final m = RecipeModel();
    await mount(t, m);
    m.reject = true;
    await click(t, 'Review settings');
    expect(find.textContaining('Library changed'), findsOneWidget);
    expect(enabled(t), false);
    expect(find.text('Review settings'), findsNothing);
    m.reject = false;
    await click(t, 'Refresh choices');
    await click(t, 'Review settings');
    expect(m.requests.last['params']['token'], 'choices-2');
    expect(m.commands, isEmpty);
    expect(t.takeException(), isNull);
  });
  testWidgets('Unverified stock prevents recipe review', (t) async {
    final m = RecipeModel();
    m.fixture['listing']['stock']['verified'] = false;
    await mount(t, m);
    expect(
      t
          .widget<OutlinedButton>(
            find.widgetWithText(OutlinedButton, 'Review settings'),
          )
          .onPressed,
      isNull,
    );
    expect(enabled(t), false);
    expect(find.textContaining('Verify physical stock'), findsOneWidget);
    expect(m.commands, isEmpty);
  });
  testWidgets(
    'Manufacturing routes reviewed choice to actual motion and requires separate Apply',
    (t) async {
      await size(t, const Size(1500, 950));
      final m = RecipeModel();
      addTearDown(m.dispose);
      await t.pumpWidget(DesignApp(controller: m, startBackend: false));
      await t.pumpAndSettle();
      final dynamic workspace = t.state(find.byType(DesignWorkspace));
      workspace.view.scale = 17.0;
      workspace.view.translation = const Offset(111, 333);
      workspace.view.grid = true;
      workspace.view.snap = true;
      await click(t, 'Manufacturing');
      final dynamic manufacturing = t.state(
        find.byType(ManufacturingWorkspace),
      );
      final task = manufacturing.guidedReadiness() as Future<void>;
      await t.pumpAndSettle();
      await click(t, 'Attach recipe...');
      await click(t, 'Review settings');
      await click(t, 'I reviewed recipe scope and the exact setting changes.');
      await click(t, 'Generate motion review');
      await task;
      expect(
        m.requests
            .where((r) => r['command'] == 'manufacturing.attachment_prepare')
            .single['params']['reviewed'],
        true,
      );
      expect(m.commands, isEmpty);
      expect(workspace.view.scale, 17);
      expect(workspace.view.translation, const Offset(111, 333));
      expect(workspace.view.grid && workspace.view.snap, true);
      manufacturing.inspectorScroll.jumpTo(0.0);
      await t.pumpAndSettle();
      final apply = find.widgetWithText(
        FilledButton,
        'Apply recipe attachment',
      );
      await t.scrollUntilVisible(
        apply,
        300,
        scrollable: find.descendant(
          of: find.byWidgetPredicate(
            (w) =>
                w is ListView && w.controller == manufacturing.inspectorScroll,
          ),
          matching: find.byType(Scrollable),
        ),
        maxScrolls: 40,
      );
      await t.pumpAndSettle();
      expect(t.widget<FilledButton>(apply).onPressed, isNull);
      await t.scrollUntilVisible(
        find.text(
          'I reviewed the entry, depth passes, finishing/cleanout settings and motion.',
        ),
        -150,
        scrollable: find.descendant(
          of: find.byWidgetPredicate(
            (w) =>
                w is ListView && w.controller == manufacturing.inspectorScroll,
          ),
          matching: find.byType(Scrollable),
        ),
        maxScrolls: 30,
      );
      await t.pumpAndSettle();
      await click(
        t,
        'I reviewed the entry, depth passes, finishing/cleanout settings and motion.',
      );
      await click(t, 'Apply recipe attachment');
      expect(m.edits.last['command'], 'manufacturing.operation_apply');
      expect(m.edits.last['params']['token'], m.fixture['motion']['token']);
      expect(t.takeException(), isNull);
    },
  );
}
