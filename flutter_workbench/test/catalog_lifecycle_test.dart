import 'dart:convert';
import 'dart:io';
import 'dart:ui' as ui;
import 'package:flutter/material.dart';
import 'package:flutter/rendering.dart';
import 'package:flutter/services.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:workbench_prototype/design/controller.dart';
import 'package:workbench_prototype/design/catalog_entry_editor.dart';
import 'package:workbench_prototype/design/catalog_action_dialog.dart';
import 'package:workbench_prototype/design/manufacturing_tools.dart';
import 'catalog_entry_editor_test.dart' show LibraryModel, entry;

class LifecycleModel extends LibraryModel {
  Json row = entry();
  String? action;
  Json? payload;
  bool enabled = true;
  int index = 0;
  @override
  Future<Json> request(String command, [Json params = const {}]) async {
    if (command == 'manufacturing.catalog_list') {
      return {
        'ok': true,
        'catalog': [row],
        'version': 'v1',
        'editCustom': true,
        'editScope': true,
        'manageRecipes': enabled,
      };
    }
    if (command.startsWith('manufacturing.catalog_recipe_') ||
        command == 'manufacturing.catalog_tool_duplicate') {
      writes++;
      action = command;
      payload = params;
      if (stale) {
        return {
          'ok': false,
          'error': 'Tool library changed. Reopen it before saving.',
        };
      }
      if (command == 'manufacturing.catalog_recipe_add') {
        (row['recipes'] as List).add(params['recipe']);
        index = (row['recipes'] as List).length - 1;
      }
      if (command == 'manufacturing.catalog_recipe_remove') {
        (row['recipes'] as List).removeAt(params['recipe_index']);
        index = 0;
      }
      if (command == 'manufacturing.catalog_recipe_status') {
        (row['recipes'] as List)[params['recipe_index']]['qualification'] =
            params['retired'] == true ? 'retired' : 'prototype';
      }
      return {
        'ok': true,
        'catalog': [row],
        'version': 'v2',
        'selectedId': row['id'],
        'selectedRecipe': index,
      };
    }
    return super.request(command, params);
  }
}

void main() {
  setUpAll(() async {
    for (final font in {
      'Roboto': 'C:/Windows/Fonts/segoeui.ttf',
      'MaterialIcons':
          '../.flutter-sdk/bin/cache/artifacts/material_fonts/MaterialIcons-Regular.otf',
    }.entries) {
      final bytes = await File(font.value).readAsBytes();
      await (FontLoader(
        font.key,
      )..addFont(Future.value(ByteData.sublistView(bytes)))).load();
    }
  });
  Future<LifecycleModel> mount(
    WidgetTester t, {
    String? action,
    bool adding = false,
    bool compact = false,
    bool retired = false,
    bool enabled = true,
    String units = 'in',
  }) async {
    t.view.physicalSize = compact
        ? const Size(800, 600)
        : const Size(1280, 1000);
    t.view.devicePixelRatio = 1;
    addTearDown(t.view.resetPhysicalSize);
    addTearDown(t.view.resetDevicePixelRatio);
    final m = LifecycleModel()..enabled = enabled;
    m.state['units'] = units;
    if (retired) (m.row['recipes'] as List).first['qualification'] = 'retired';
    addTearDown(m.dispose);
    await t.pumpWidget(
      MaterialApp(
        home: Scaffold(
          body: TextButton(
            onPressed: () => showDialog<void>(
              context: t.element(find.byType(TextButton)),
              builder: (_) => RepaintBoundary(
                key: const ValueKey('capture'),
                child: action != null
                    ? CatalogActionDialog(
                        model: m,
                        entry: m.row,
                        version: 'v1',
                        recipeIndex: 0,
                        action: action,
                      )
                    : adding
                    ? CatalogEntryEditor(
                        model: m,
                        entry: {
                          ...m.row,
                          'recipes': [
                            <String, dynamic>{'material': ''},
                          ],
                        },
                        version: 'v1',
                        recipeIndex: 0,
                        editScope: true,
                        creating: true,
                      )
                    : ManufacturingToolsDialog(model: m, library: true),
              ),
            ),
            child: const Text('Open'),
          ),
        ),
      ),
    );
    await t.tap(find.text('Open'));
    await t.pumpAndSettle();
    return m;
  }

  Future<void> click(WidgetTester t, Finder f) async {
    await t.ensureVisible(f);
    await t.pumpAndSettle();
    await t.tap(f);
    await t.pumpAndSettle();
  }

  Finder input(String label) => find.widgetWithText(TextField, label);
  Future<void> fill(WidgetTester t, String label, String value) async {
    await t.ensureVisible(input(label));
    await t.enterText(input(label), value);
  }

  testWidgets(
    'New metric recipe form sends explicit units and no cutter or qualification changes',
    (t) async {
      final m = await mount(t, adding: true, units: 'mm');
      final before = jsonEncode(m.state);
      expect(input('Tool name'), findsNothing);
      expect(find.text('Qualification record'), findsNothing);
      await fill(t, 'Material / recipe name', 'Plywood finish');
      await fill(t, 'Recipe RPM (blank if disabled)', '18000');
      await fill(t, 'Cut feed (mm/min)', '2286');
      await fill(t, 'Plunge feed (mm/min)', '762');
      await fill(t, 'Stepdown (mm)', '2.54');
      await fill(t, 'Minimum thickness (mm, optional)', '6.35');
      await fill(t, 'Maximum thickness (mm, optional)', '19.05');
      await click(t, find.text('Save library entry'));
      expect(m.action, 'manufacturing.catalog_recipe_add');
      expect(m.payload!['units'], 'mm');
      expect(m.payload!['recipe']['feed'], 2286);
      expect(m.payload!.containsKey('tool'), false);
      expect(m.payload!.containsKey('qualification'), false);
      expect(jsonEncode(m.state), before);
    },
  );
  testWidgets('Blank or invalid new recipe stays unsaved', (t) async {
    final m = await mount(t, adding: true);
    await click(t, find.text('Save library entry'));
    expect(m.writes, 0);
    expect(find.textContaining('must be a positive'), findsWidgets);
  });
  testWidgets(
    'Duplicate recipe and custom tool send reviewed name and version',
    (t) async {
      for (final action in [
        'catalog_recipe_duplicate',
        'catalog_tool_duplicate',
      ]) {
        final m = await mount(t, action: action);
        final before = jsonEncode(m.state);
        await t.enterText(input('New name'), 'Reviewed copy');
        await click(t, find.byType(FilledButton));
        expect(m.payload!['name'], 'Reviewed copy');
        expect(m.payload!['version'], 'v1');
        expect(
          m.payload!.containsKey('recipe_index'),
          action == 'catalog_recipe_duplicate',
        );
        expect(jsonEncode(m.state), before);
        await t.pumpWidget(const SizedBox());
      }
    },
  );
  testWidgets(
    'Remove and retire require explicit confirmation; Cancel writes nothing',
    (t) async {
      final m = await mount(t, action: 'catalog_recipe_remove');
      await click(t, find.text('Cancel'));
      expect(m.writes, 0);
      final n = await mount(t, action: 'catalog_recipe_status');
      await click(t, find.byType(FilledButton));
      expect(n.payload!['confirmed'], true);
      expect(n.payload!['retired'], true);
      final r = await mount(t, action: 'catalog_recipe_status', retired: true);
      expect(find.textContaining('Reactivate as Prototype'), findsOneWidget);
      await click(t, find.byType(FilledButton));
      expect(r.payload!['retired'], false);
    },
  );
  testWidgets('Stale actions preserve entered name and remain open', (t) async {
    final m = await mount(t, action: 'catalog_tool_duplicate');
    m.stale = true;
    await t.enterText(input('New name'), 'Keep this name');
    await click(t, find.byType(FilledButton));
    expect(find.textContaining('library changed'), findsOneWidget);
    expect(
      t.widget<TextField>(input('New name')).controller!.text,
      'Keep this name',
    );
  });
  testWidgets(
    'Library exposes all five actions and protects retired and last recipes',
    (t) async {
      await mount(t, retired: true);
      for (final label in [
        'Add recipe',
        'Duplicate recipe',
        'Remove recipe',
        'Reactivate recipe',
        'Duplicate custom tool',
      ]) {
        expect(find.text(label), findsOneWidget);
      }
      expect(
        t
            .widget<TextButton>(
              find.widgetWithText(TextButton, 'Remove recipe'),
            )
            .onPressed,
        isNull,
      );
      expect(
        t
            .widget<FilledButton>(
              find.widgetWithText(FilledButton, 'Load into T1 draft'),
            )
            .onPressed,
        isNull,
      );
      expect(
        t
            .widget<TextButton>(
              find.widgetWithText(TextButton, 'Edit custom tool / recipe'),
            )
            .onPressed,
        isNull,
      );
      await click(t, find.text('Add recipe'));
      expect(find.byType(CatalogEntryEditor), findsOneWidget);
      expect(t.takeException(), isNull);
    },
  );
  testWidgets(
    'Older backend omits lifecycle actions without blocking library',
    (t) async {
      await mount(t, enabled: false);
      expect(find.text('Add recipe'), findsNothing);
      expect(find.text('Edit custom tool / recipe'), findsOneWidget);
    },
  );
  testWidgets('Compact library keeps actions reachable without overflow', (
    t,
  ) async {
    await mount(t, compact: true);
    expect(t.takeException(), isNull);
    await t.ensureVisible(find.text('Duplicate custom tool'));
    await t.pumpAndSettle();
    await t.runAsync(() async {
      final boundary = t.renderObject<RenderRepaintBoundary>(
        find.byKey(const ValueKey('capture')),
      );
      final picture = await boundary.toImage();
      final bytes = await picture.toByteData(format: ui.ImageByteFormat.png);
      await Directory('build/ui-review').create(recursive: true);
      await File(
        'build/ui-review/library-five-compact.png',
      ).writeAsBytes(bytes!.buffer.asUint8List());
      picture.dispose();
    });
    await click(t, find.text('Duplicate recipe'));
    expect(find.byType(CatalogActionDialog), findsOneWidget);
  });
}
