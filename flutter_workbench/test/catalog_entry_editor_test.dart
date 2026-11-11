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
import 'manufacturing_test.dart' show ManufacturingModel;

Json entry() => {
  'id': 'custom-cutter',
  'custom': true,
  'name': 'Coupon cutter',
  'kind': 'end_mill',
  'diameter_in': .25,
  'cutting_edge_length_in': .75,
  'spindle_mode': 'required',
  'min_rpm': 8000,
  'max_rpm': 24000,
  'recipes': [
    <String, dynamic>{
      'material': 'Plywood',
      'rpm': 18000,
      'feed': 90,
      'plunge': 30,
      'stepdown': .125,
      'stepover': .1,
    },
  ],
};

class LibraryModel extends ManufacturingModel {
  Json? update;
  bool stale = false, editing = true;
  int writes = 0;
  @override
  Future<Json> request(String command, [Json params = const {}]) async {
    if (command == 'manufacturing.catalog_list') {
      return {
        'ok': true,
        'catalog': [entry()],
        'version': 'version-before',
        'editCustom': editing,
      };
    }
    if (command == 'manufacturing.catalog_update') {
      writes++;
      update = params;
      return stale
          ? {
              'ok': false,
              'error': 'Tool library changed. Reopen it before saving.',
            }
          : {
              'ok': true,
              'catalog': [entry()],
              'version': 'version-after',
              'downgradedRecipes': [0],
            };
    }
    return super.request(command, params);
  }
}

void main() {
  setUpAll(() async {
    final data = await File('C:/Windows/Fonts/segoeui.ttf').readAsBytes();
    await (FontLoader(
      'Roboto',
    )..addFont(Future.value(ByteData.sublistView(data)))).load();
  });
  Future<LibraryModel> mount(
    WidgetTester t, {
    String units = 'in',
    bool compact = false,
    bool library = false,
    bool editing = true,
  }) async {
    t.view.physicalSize = compact
        ? const Size(800, 600)
        : const Size(1200, 1000);
    t.view.devicePixelRatio = 1;
    addTearDown(t.view.resetPhysicalSize);
    addTearDown(t.view.resetDevicePixelRatio);
    final m = LibraryModel();
    m.state['units'] = units;
    m.editing = editing;
    addTearDown(m.dispose);
    await t.pumpWidget(
      MaterialApp(
        home: Scaffold(
          body: Builder(
            builder: (context) => TextButton(
              onPressed: () => showDialog(
                context: context,
                builder: (_) => library
                    ? ManufacturingToolsDialog(model: m, library: true)
                    : RepaintBoundary(
                        key: const ValueKey('catalog-editor-image'),
                        child: CatalogEntryEditor(
                          model: m,
                          entry: entry(),
                          version: 'version-before',
                          recipeIndex: 0,
                        ),
                      ),
              ),
              child: const Text('Open editor'),
            ),
          ),
        ),
      ),
    );
    await t.tap(find.text('Open editor'));
    await t.pumpAndSettle();
    return m;
  }

  Finder input(String label) => find.widgetWithText(TextField, label);
  Future<void> scrollTo(WidgetTester t, Finder f) async {
    await t.ensureVisible(f);
    await t.pumpAndSettle();
  }

  testWidgets(
    'Metric editor sends displayed units and stable version without touching the job',
    (t) async {
      final m = await mount(t, units: 'mm');
      final original = jsonEncode(m.state);
      expect(
        t.widget<TextField>(input('Diameter (mm)')).controller!.text,
        '6.35',
      );
      expect(
        t.widget<TextField>(input('Cut feed (mm/min)')).controller!.text,
        '2286.0',
      );
      await t.tap(find.text('Save library entry'));
      await t.pumpAndSettle();
      expect(m.update!['id'], 'custom-cutter');
      expect(m.update!['version'], 'version-before');
      expect(m.update!['units'], 'mm');
      expect(m.update!['tool']['diameter'], 6.35);
      expect(m.update!['recipe']['feed'], 2286);
      expect(m.update!['recipe']['rpm'], 18000);
      expect(jsonEncode(m.state), original);
      expect(m.calls, isEmpty);
    },
  );
  testWidgets(
    'Stale save preserves the draft and displays the version conflict',
    (t) async {
      final m = await mount(t);
      m.stale = true;
      await t.enterText(input('Tool name'), 'Keep my unsaved catalog name');
      await t.tap(find.text('Save library entry'));
      await t.pumpAndSettle();
      expect(find.byType(CatalogEntryEditor), findsOneWidget);
      expect(
        t.widget<TextField>(input('Tool name')).controller!.text,
        'Keep my unsaved catalog name',
      );
      expect(find.textContaining('Tool library changed.'), findsOneWidget);
      await t.tap(find.text('Cancel'));
      await t.pumpAndSettle();
      expect(m.writes, 1);
    },
  );
  testWidgets('Compact form scrolls and cancellation sends no mutation', (
    t,
  ) async {
    final m = await mount(t, compact: true);
    await scrollTo(
      t,
      input('Evidence: material/thickness, setup, date and result'),
    );
    await t.enterText(
      input('Evidence: material/thickness, setup, date and result'),
      'Coupon notes',
    );
    expect(t.takeException(), isNull);
    await t.runAsync(() async {
      final boundary = t.renderObject<RenderRepaintBoundary>(
        find.byKey(const ValueKey('catalog-editor-image')),
      );
      final image = await boundary.toImage();
      final bytes = await image.toByteData(format: ui.ImageByteFormat.png);
      await Directory('build/ui-review').create(recursive: true);
      await File(
        'build/ui-review/catalog-editor-compact.png',
      ).writeAsBytes(bytes!.buffer.asUint8List());
      image.dispose();
    });
    await t.tap(find.text('Cancel'));
    await t.pumpAndSettle();
    expect(m.writes, 0);
  });
  testWidgets('Nonfinite and fractional RPM never reach the backend', (
    t,
  ) async {
    final m = await mount(t);
    await t.enterText(input('Diameter (in)'), 'NaN');
    await t.tap(find.text('Save library entry'));
    await t.pumpAndSettle();
    expect(m.writes, 0);
    expect(find.textContaining('diameter must be a positive'), findsOneWidget);
    await t.enterText(input('Diameter (in)'), '.25');
    await scrollTo(t, input('Recipe RPM (blank if disabled)'));
    await t.enterText(input('Recipe RPM (blank if disabled)'), '18000.1');
    await t.tap(find.text('Save library entry'));
    await t.pumpAndSettle();
    expect(m.writes, 0);
    expect(find.textContaining('rpm must be a positive whole'), findsOneWidget);
  });
  testWidgets(
    'Library exposes editing and reports a downgrade without loading the turret',
    (t) async {
      final m = await mount(t, library: true);
      final original = jsonEncode(m.state);
      await t.tap(find.text('Edit custom tool / recipe'));
      await t.pumpAndSettle();
      expect(find.byType(CatalogEntryEditor), findsOneWidget);
      await t.tap(find.text('Save library entry'));
      await t.pumpAndSettle();
      expect(find.textContaining('need a new test record'), findsOneWidget);
      expect(jsonEncode(m.state), original);
      expect(m.calls, isEmpty);
    },
  );
  testWidgets('Older backend explains unavailable editing', (t) async {
    await mount(t, library: true, editing: false);
    expect(
      t
          .widget<TextButton>(
            find.widgetWithText(TextButton, 'Edit custom tool / recipe'),
          )
          .onPressed,
      isNull,
    );
    expect(
      find.text('Save and reopen this drawing to enable library editing.'),
      findsOneWidget,
    );
  });
}
