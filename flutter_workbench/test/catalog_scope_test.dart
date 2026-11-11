import 'dart:convert';
import 'dart:io';
import 'dart:ui' as ui;
import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter/rendering.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:workbench_prototype/design/controller.dart';
import 'package:workbench_prototype/design/catalog_entry_editor.dart';
import 'package:workbench_prototype/design/manufacturing_tools.dart';
import 'package:workbench_prototype/design/workspace.dart';
import 'catalog_entry_editor_test.dart' show LibraryModel, entry;
import 'design_workspace_test.dart' show TestDesignController;

Json scopedEntry() {
  final row = entry();
  (row['recipes'] as List).first.addAll({
    'material_family': 'plywood',
    'thickness_min_in': .25,
    'thickness_max_in': .75,
    'process_roles': ['outside_profile'],
    'entry_strategies': ['plunge'],
  });
  return row;
}

class ScopeModel extends LibraryModel {
  bool scopeSupported = true;
  @override
  Future<Json> request(String command, [Json params = const {}]) async {
    final r = await super.request(command, params);
    if (command == 'manufacturing.catalog_list') {
      r['editScope'] = scopeSupported;
      r['catalog'] = [scopedEntry()];
    }
    return r;
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
  Future<ScopeModel> mount(
    WidgetTester t, {
    String units = 'in',
    bool compact = false,
    bool parent = false,
    bool enabled = true,
  }) async {
    t.view.physicalSize = compact
        ? const Size(800, 600)
        : const Size(1100, 1000);
    t.view.devicePixelRatio = 1;
    addTearDown(t.view.resetPhysicalSize);
    addTearDown(t.view.resetDevicePixelRatio);
    final m = ScopeModel();
    m.state['units'] = units;
    m.scopeSupported = enabled;
    addTearDown(m.dispose);
    await t.pumpWidget(
      MaterialApp(
        home: Scaffold(
          body: Builder(
            builder: (context) => TextButton(
              onPressed: () => showDialog<void>(
                context: context,
                builder: (_) => parent
                    ? ManufacturingToolsDialog(model: m, library: true)
                    : RepaintBoundary(
                        key: const ValueKey('scope-image'),
                        child: CatalogEntryEditor(
                          model: m,
                          entry: scopedEntry(),
                          version: 'scope-v',
                          recipeIndex: 0,
                          editScope: enabled,
                        ),
                      ),
              ),
              child: const Text('Open'),
            ),
          ),
        ),
      ),
    );
    await t.tap(find.text('Open'));
    await t.pumpAndSettle();
    return m;
  }

  Finder field(String label) => find.widgetWithText(TextField, label);
  Future<void> show(WidgetTester t, Finder f) async {
    await t.ensureVisible(f);
    await t.pumpAndSettle();
  }

  Future<void> chip(WidgetTester t, String label) async {
    final f = find.widgetWithText(FilterChip, label);
    await show(t, f);
    await t.tap(f);
    await t.pumpAndSettle();
  }

  testWidgets(
    'Metric scope edits send displayed dimensions with stable identity and no job mutation',
    (t) async {
      final m = await mount(t, units: 'mm');
      final before = jsonEncode(m.state);
      final lower = field('Minimum thickness (mm, optional)');
      await show(t, lower);
      expect(t.widget<TextField>(lower).controller!.text, '6.35');
      await t.enterText(lower, '5');
      await t.enterText(field('Maximum thickness (mm, optional)'), '20');
      await t.tap(find.text('Save library entry'));
      await t.pumpAndSettle();
      expect(m.update!['scope'], containsPair('thickness_min', 5));
      expect(m.update!['scope'], containsPair('thickness_max', 20));
      expect(m.update!['units'], 'mm');
      expect(m.update!['version'], 'scope-v');
      expect(jsonEncode(m.state), before);
    },
  );
  testWidgets(
    'Inverted and nonfinite bounds are refused before a library write',
    (t) async {
      final m = await mount(t);
      final lower = field('Minimum thickness (in, optional)');
      await show(t, lower);
      await t.enterText(lower, '2');
      await t.tap(find.text('Save library entry'));
      await t.pumpAndSettle();
      expect(m.writes, 0);
      expect(find.textContaining('Minimum thickness exceeds'), findsOneWidget);
      await show(t, lower);
      await t.enterText(lower, 'NaN');
      await t.tap(find.text('Save library entry'));
      await t.pumpAndSettle();
      expect(m.writes, 0);
    },
  );
  testWidgets(
    'Any role is exclusive and entry restrictions can be explicitly cleared',
    (t) async {
      final m = await mount(t);
      await chip(t, 'Inside profile');
      await chip(t, 'Any role');
      await chip(t, 'Helical');
      await show(t, find.text('Clear entry restrictions'));
      await t.tap(find.text('Clear entry restrictions'));
      await t.tap(find.text('Save library entry'));
      await t.pumpAndSettle();
      expect(m.update!['scope']['process_roles'], ['any']);
      expect(m.update!['scope']['entry_strategies'], isEmpty);
    },
  );
  testWidgets(
    'Stale save retains applicability draft and tells user to reopen',
    (t) async {
      final m = await mount(t);
      m.stale = true;
      final family = field('Material family');
      await show(t, family);
      await t.enterText(family, 'Aluminium');
      await t.tap(find.text('Save library entry'));
      await t.pumpAndSettle();
      expect(find.textContaining('Tool library changed'), findsOneWidget);
      expect(t.widget<TextField>(family).controller!.text, 'Aluminium');
    },
  );
  testWidgets(
    'Compact applicability controls render and Cancel leaves job and library unchanged',
    (t) async {
      final m = await mount(t, compact: true);
      final before = jsonEncode(m.state);
      await show(t, find.text('Recipe applicability'));
      await t.pumpAndSettle();
      expect(t.takeException(), isNull);
      await t.runAsync(() async {
        final boundary = t.renderObject<RenderRepaintBoundary>(
          find.byKey(const ValueKey('scope-image')),
        );
        final picture = await boundary.toImage();
        final bytes = await picture.toByteData(format: ui.ImageByteFormat.png);
        await Directory('build/ui-review').create(recursive: true);
        await File(
          'build/ui-review/recipe-scope-compact.png',
        ).writeAsBytes(bytes!.buffer.asUint8List());
        picture.dispose();
      });
      await t.tap(find.text('Cancel'));
      await t.pumpAndSettle();
      expect(m.writes, 0);
      expect(jsonEncode(m.state), before);
    },
  );
  testWidgets(
    'Library honors backend scope capability without blocking older editing',
    (t) async {
      final m = await mount(t, parent: true, enabled: false);
      await t.tap(find.text('Edit custom tool / recipe'));
      await t.pumpAndSettle();
      expect(find.text('Recipe applicability'), findsNothing);
      expect(
        find.textContaining('enable recipe applicability'),
        findsOneWidget,
      );
      await t.tap(find.text('Save library entry'));
      await t.pumpAndSettle();
      expect(m.update!.containsKey('scope'), isFalse);
    },
  );
  testWidgets('Cabinet imports show the explicit view-only reason', (t) async {
    t.view.physicalSize = const Size(1400, 1000);
    t.view.devicePixelRatio = 1;
    addTearDown(t.view.resetPhysicalSize);
    addTearDown(t.view.resetDevicePixelRatio);
    final m = TestDesignController();
    m.state['readOnly'] = true;
    m.state['readOnlyReason'] =
        'Cabinet-tab registration is view-only until its review workflow is ported.';
    addTearDown(m.dispose);
    await t.pumpWidget(DesignApp(controller: m, startBackend: false));
    await t.pumpAndSettle();
    expect(find.text(m.state['readOnlyReason']), findsOneWidget);
    expect(t.takeException(), isNull);
  });
}
