import 'dart:convert';
import 'dart:io';
import 'dart:ui' as ui;
import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter/rendering.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:workbench_prototype/design/controller.dart';
import 'package:workbench_prototype/design/catalog_matches_dialog.dart';
import 'package:workbench_prototype/design/manufacturing_tools.dart';
import 'manufacturing_test.dart' show ManufacturingModel;

class MatchingModel extends ManufacturingModel {
  final requests = <String>[];
  Json? resolved, lastQuery, turretDraft;
  double get testFeed => state['units'] == 'mm' ? 1.23456789012345 * 25.4 : 1.5;
  bool rejectMatch = false;
  void revise() {
    state['revision'] = (state['revision'] as int) + 1;
    notifyListeners();
  }

  @override
  Future<Json> request(String command, [Json params = const {}]) async {
    requests.add(command);
    if (command == 'manufacturing.catalog_list') {
      return {
        'ok': true,
        'catalog': <Json>[],
        'version': 'v',
        'matchRecipes': true,
      };
    }
    if (command == 'manufacturing.catalog_matches') {
      lastQuery = params;
      return {
        'ok': true,
        'version': 'v',
        'token': 'job-and-cutter-token',
        'units': state['units'],
        'material': 'Plywood',
        'thickness': state['units'] == 'mm' ? 12.7 : .5,
        'affected_operations': [
          {'name': 'Outside profile'},
        ],
        'matches': [
          {
            'id': 'quarter',
            'recipe': 0,
            'name': 'Plywood coupon',
            'tool_name': 'Quarter inch',
            'qualification': 'shop_tested',
            'evidence': 'Coupon 14: plywood, vacuum table, clean edge.',
            'notes': 'Inspect the first cut.',
            'eligible': true,
            'reasons': <String>[],
            'warnings': ['Not production approved; verify this job.'],
            'scope': {
              'material_family': 'plywood',
              'thickness_min_in': .25,
              'thickness_max_in': .75,
              'process_roles': ['outside_profile'],
            },
            'values': {
              'rpm': 18000,
              'feed': 90,
              'plunge': 30,
              'stepdown': .125,
              'stepover': .1,
            },
          },
          {
            'id': 'other',
            'recipe': 0,
            'name': 'Aluminum recipe',
            'tool_name': 'Other cutter',
            'qualification': 'retired',
            'evidence': '',
            'notes': '',
            'eligible': false,
            'reasons': ['Stock material does not match.', 'Recipe is retired.'],
            'warnings': <String>[],
            'scope': <String, dynamic>{},
            'values': <String, dynamic>{},
          },
        ],
      };
    }
    if (command == 'manufacturing.catalog_match_resolve') {
      resolved = params;
      return rejectMatch
          ? {
              'ok': false,
              'error':
                  'Library changed. Refresh recipe matches before loading.',
            }
          : {
              'ok': true,
              'note': 'Review regenerated motion before Apply.',
              'tool': <String, dynamic>{
                'station': 1,
                'kind': 'end_mill',
                'name': 'Quarter inch',
                'diameter': .25,
                'feed_rate': testFeed,
                'plunge_rate': .5,
                'spindle_rpm': 18000,
                'spindle_mode': 'required',
                'metadata': <String, dynamic>{
                  'loaded_recipe': {
                    'material': 'Plywood',
                    'qualification': 'shop_tested',
                  },
                },
              },
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
    final icons = await File(
      '../.flutter-sdk/bin/cache/artifacts/material_fonts/MaterialIcons-Regular.otf',
    ).readAsBytes();
    await (FontLoader(
      'MaterialIcons',
    )..addFont(Future.value(ByteData.sublistView(icons)))).load();
  });
  Future<MatchingModel> mount(
    WidgetTester t, {
    bool compact = false,
    bool parent = false,
    String units = 'in',
  }) async {
    t.view.physicalSize = compact
        ? const Size(800, 600)
        : const Size(1100, 1000);
    t.view.devicePixelRatio = 1;
    addTearDown(t.view.resetPhysicalSize);
    addTearDown(t.view.resetDevicePixelRatio);
    final m = MatchingModel();
    m.state['units'] = units;
    await t.pumpWidget(
      MaterialApp(
        home: Scaffold(
          body: Builder(
            builder: (context) => TextButton(
              onPressed: () => showDialog<Json>(
                context: context,
                builder: (_) => parent
                    ? ManufacturingToolsDialog(model: m, library: true)
                    : RepaintBoundary(
                        key: const ValueKey('matches-image'),
                        child: CatalogMatchesDialog(model: m, station: 1),
                      ),
              ).then((value) => m.turretDraft = value),
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

  Finder getLoad() => find.widgetWithText(FilledButton, 'Load T1 draft');
  Future<void> selectAndReview(WidgetTester t) async {
    await t.tap(find.byKey(const ValueKey('match-quarter-0')));
    await t.pumpAndSettle();
    await t.scrollUntilVisible(
      find.byType(CheckboxListTile),
      180,
      scrollable: find
          .descendant(
            of: find.byType(CatalogMatchesDialog),
            matching: find.byType(Scrollable),
          )
          .last,
    );
    await t.tap(find.byType(Checkbox));
    await t.pumpAndSettle();
  }

  testWidgets('Explicit scope review loads only a private station draft', (
    t,
  ) async {
    final m = await mount(t);
    final before = jsonEncode(m.state);
    expect(t.widget<FilledButton>(getLoad()).onPressed, isNull);
    await selectAndReview(t);
    expect(find.textContaining('Coupon 14'), findsOneWidget);
    await t.tap(getLoad());
    await t.pumpAndSettle();
    expect(m.resolved, containsPair('reviewed', true));
    expect(m.resolved, containsPair('token', 'job-and-cutter-token'));
    expect(m.calls, isEmpty);
    expect(jsonEncode(m.state), before);
  });
  testWidgets('Excluded entries explain refusal and cannot load', (t) async {
    final m = await mount(t);
    await t.tap(find.byType(Switch));
    await t.pumpAndSettle();
    await t.tap(find.byKey(const ValueKey('match-other-0')));
    await t.pumpAndSettle();
    expect(find.textContaining('Stock material does not match'), findsWidgets);
    expect(t.widget<FilledButton>(getLoad()).onPressed, isNull);
    expect(m.resolved, isNull);
  });
  testWidgets('Role change clears choice and requests new matches', (t) async {
    final m = await mount(t);
    await selectAndReview(t);
    await t.tap(find.byType(DropdownButtonFormField<String>));
    await t.pumpAndSettle();
    await t.tap(find.text('Pocket / cleanout').last);
    await t.pumpAndSettle();
    expect(m.lastQuery, containsPair('process_role', 'pocket_fill'));
    expect(t.widget<FilledButton>(getLoad()).onPressed, isNull);
  });
  testWidgets(
    'Job changes and cross-process library changes require review again',
    (t) async {
      final m = await mount(t);
      await selectAndReview(t);
      m.revise();
      await t.pumpAndSettle();
      expect(t.widget<FilledButton>(getLoad()).onPressed, isNull);
      await t.tap(find.byTooltip('Refresh recipe matches'));
      await t.pumpAndSettle();
      await selectAndReview(t);
      m.rejectMatch = true;
      await t.tap(getLoad());
      await t.pumpAndSettle();
      expect(t.widget<FilledButton>(getLoad()).onPressed, isNull);
      expect(find.byType(CatalogMatchesDialog), findsOneWidget);
      await t.drag(find.byType(ListView), const Offset(0, 900));
      await t.pumpAndSettle();
      expect(find.textContaining('Library changed'), findsOneWidget);
    },
  );
  testWidgets(
    'Compact metric review scrolls, renders and cancels without mutation',
    (t) async {
      final m = await mount(t, compact: true, units: 'mm');
      await selectAndReview(t);
      expect(find.textContaining('2286.0 mm/min'), findsOneWidget);
      expect(t.takeException(), isNull);
      await t.runAsync(() async {
        final boundary = t.renderObject<RenderRepaintBoundary>(
          find.byKey(const ValueKey('matches-image')),
        );
        final image = await boundary.toImage();
        final bytes = await image.toByteData(format: ui.ImageByteFormat.png);
        await Directory('build/ui-review').create(recursive: true);
        await File(
          'build/ui-review/recipe-matches-compact.png',
        ).writeAsBytes(bytes!.buffer.asUint8List());
        image.dispose();
      });
      await t.tap(find.text('Cancel'));
      await t.pumpAndSettle();
      expect(m.resolved, isNull);
      expect(m.calls, isEmpty);
    },
  );
  testWidgets(
    'Library routes matched station to turret review without applying',
    (t) async {
      final m = await mount(t, parent: true);
      final before = jsonEncode(m.state);
      await t.tap(find.text('Match job recipes'));
      await t.pumpAndSettle();
      await selectAndReview(t);
      await t.tap(getLoad());
      await t.pumpAndSettle();
      expect(find.text('Turret Setup'), findsOneWidget);
      expect(find.text('Review turret changes'), findsOneWidget);
      expect(find.text('Quarter inch'), findsOneWidget);
      expect(jsonEncode(m.state), before);
      expect(m.calls, isEmpty);
    },
  );
  testWidgets(
    'Unedited metric feeds retain exact values and recipe evidence at review',
    (t) async {
      final m = await mount(t, parent: true, units: 'mm');
      await t.tap(find.text('Match job recipes'));
      await t.pumpAndSettle();
      await selectAndReview(t);
      await t.tap(getLoad());
      await t.pumpAndSettle();
      await t.tap(find.text('Review turret changes'));
      await t.pumpAndSettle();
      final tool = (m.turretDraft!['tools'] as List).single as Json;
      expect(tool['feed_rate'], m.testFeed);
      expect(tool['metadata']['loaded_recipe']['qualification'], 'shop_tested');
      expect(m.calls, isEmpty);
    },
  );
}
