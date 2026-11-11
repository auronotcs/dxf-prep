import 'dart:convert';
import 'dart:io';
import 'dart:ui' as ui;
import 'package:flutter/material.dart';
import 'package:flutter/gestures.dart';
import 'package:flutter/rendering.dart';
import 'package:flutter/services.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:workbench_prototype/design/controller.dart';
import 'package:workbench_prototype/design/canvas.dart';
import 'package:workbench_prototype/design/manufacturing_picker.dart';
import 'package:workbench_prototype/design/manufacturing_workspace.dart';
import 'manufacturing_expanded_test.dart' show ExpandedModel;

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
  Json? result;
  final canvas = find.byKey(const ValueKey('manufacturing-picker-canvas'));
  DesignViewport view(WidgetTester t) =>
      (t.state(find.byType(ManufacturingPicker)) as dynamic).view
          as DesignViewport;
  Offset at(WidgetTester t, Offset p) =>
      t.getTopLeft(canvas) + view(t).screen(p);
  Future<ExpandedModel> mount(
    WidgetTester t,
    String kind, {
    bool compact = false,
  }) async {
    result = null;
    t.view.physicalSize = compact
        ? const Size(800, 600)
        : const Size(1400, 950);
    t.view.devicePixelRatio = 1;
    addTearDown(t.view.resetPhysicalSize);
    addTearDown(t.view.resetDevicePixelRatio);
    final m = ExpandedModel();
    addTearDown(m.dispose);
    m.state['entities'].add(<String, dynamic>{
      'id': 'other',
      'kind': 'rectangle',
      'layer': '0',
      'corner_a': {'x': 8, 'y': 2},
      'corner_b': {'x': 10, 'y': 4},
      'bounds': [8, 2, 10, 4],
    });
    final info = <String, dynamic>{
      'kind': kind,
      'fingerprint': 'current',
      'eligible_ids': ['entity_123456', 'other'],
      'operation': {
        'id': 'op',
        'name': 'Profile',
        'entity_ids': ['entity_123456'],
        'island_entity_ids': <String>[],
        'manual_contour_order': <String>[],
        'start_index': 0,
        'tab_width': .25,
        'tab_height': .05,
      },
      'shape': {
        'starts': [
          {'x': 2, 'y': 2},
          {'x': 6, 'y': 2},
          {'x': 6, 'y': 4},
          {'x': 2, 'y': 4},
        ],
        'path': [
          {'x': 2, 'y': 2},
          {'x': 6, 'y': 2},
          {'x': 6, 'y': 4},
          {'x': 2, 'y': 4},
        ],
      },
      'tab_points': <Json>[],
    };
    await t.pumpWidget(
      RepaintBoundary(
        key: const ValueKey('picker-image'),
        child: MaterialApp(
          home: Builder(
            builder: (c) => Scaffold(
              body: TextButton(
                onPressed: () async {
                  result = await showDialog<Json>(
                    context: c,
                    builder: (_) => ManufacturingPicker(model: m, info: info),
                  );
                },
                child: const Text('Open picker'),
              ),
            ),
          ),
        ),
      ),
    );
    await t.tap(find.text('Open picker'));
    await t.pumpAndSettle();
    return m;
  }

  Future<void> click(WidgetTester t, Offset p) async {
    await t.tapAt(at(t, p));
    await t.pumpAndSettle();
  }

  Future<void> capture(WidgetTester t, String name) async {
    final b = t.renderObject<RenderRepaintBoundary>(
      find.byKey(const ValueKey('picker-image')),
    );
    await t.runAsync(() async {
      final image = await b.toImage();
      final bytes = await image.toByteData(format: ui.ImageByteFormat.png);
      Directory('build/ui-review').createSync(recursive: true);
      File(
        'build/ui-review/picking-$name.png',
      ).writeAsBytesSync(bytes!.buffer.asUint8List());
      image.dispose();
    });
  }

  testWidgets(
    'Start marker picking survives picker zoom/pan without changing source selection',
    (t) async {
      final m = await mount(t, 'start');
      final before = jsonEncode(m.state);
      final oldScale = view(t).scale;
      await t.sendEventToBinding(
        PointerScrollEvent(
          position: at(t, const Offset(4, 3)),
          scrollDelta: const Offset(0, -30),
        ),
      );
      await t.pumpAndSettle();
      expect(view(t).scale, greaterThan(oldScale));
      await click(t, const Offset(6, 2));
      await capture(t, 'start');
      await t.tap(find.text('Review picked changes'));
      await t.pumpAndSettle();
      expect(result?['index'], 1);
      expect(result?['fingerprint'], 'current');
      expect(jsonEncode(m.state), before);
      expect(m.calls, isEmpty);
    },
  );
  testWidgets('Tab add remove finite validation and compensated XY draft', (
    t,
  ) async {
    await mount(t, 'tabs');
    await click(t, const Offset(3, 2));
    await click(t, const Offset(5, 4));
    await click(t, const Offset(3, 2));
    expect(find.text('1 proposed tabs'), findsOneWidget);
    await t.enterText(find.byType(TextField).first, 'NaN');
    await t.tap(find.text('Review picked changes'));
    await t.pumpAndSettle();
    expect(find.textContaining('positive finite'), findsOneWidget);
    expect(result, isNull);
    await t.enterText(find.byType(TextField).first, '.3');
    await click(t, const Offset(3, 2));
    await capture(t, 'tabs');
    await t.tap(find.text('Review picked changes'));
    await t.pumpAndSettle();
    expect(result?['points'], hasLength(2));
    expect(result?['tab_width'], .3);
    expect(result?['kind'], 'tabs');
  });
  testWidgets('Boundary clicks include and exclude exact contours', (t) async {
    final m = await mount(t, 'boundaries');
    final before = jsonEncode(m.state);
    await click(t, const Offset(8, 3));
    await capture(t, 'boundaries');
    await t.tap(find.text('Review picked changes'));
    await t.pumpAndSettle();
    expect(result?['ids'], ['entity_123456', 'other']);
    expect(jsonEncode(m.state), before);
  });
  testWidgets(
    'Island picks are separate from outer boundaries and cancel is inert',
    (t) async {
      final m = await mount(t, 'islands');
      final before = jsonEncode(m.state);
      await click(t, const Offset(8, 3));
      await t.tap(find.text('Cancel'));
      await t.pumpAndSettle();
      expect(result, isNull);
      expect(jsonEncode(m.state), before);
      expect(m.calls, isEmpty);
      await t.tap(find.text('Open picker'));
      await t.pumpAndSettle();
      await click(t, const Offset(8, 3));
      await t.tap(find.text('Review picked changes'));
      await t.pumpAndSettle();
      expect(result?['ids'], ['other']);
      expect(result?['kind'], 'islands');
    },
  );
  testWidgets('Contour click order and automatic restoration at compact size', (
    t,
  ) async {
    await mount(t, 'order', compact: true);
    await click(t, const Offset(8, 3));
    await click(t, const Offset(2, 3));
    await capture(t, 'compact-order');
    await t.tap(find.text('Review picked changes'));
    await t.pumpAndSettle();
    expect(result?['ids'], ['other', 'entity_123456']);
    await t.tap(find.text('Open picker'));
    await t.pumpAndSettle();
    await t.tap(find.text('Use automatic order'));
    await t.pumpAndSettle();
    expect(result?['automatic'], true);
    expect(result?['ids'], isEmpty);
    expect(t.takeException(), isNull);
  });
  testWidgets('Changed document invalidates picker submission', (t) async {
    final m = await mount(t, 'start');
    m.state['revision']++;
    m.notifyListeners();
    await t.pumpAndSettle();
    expect(find.textContaining('drawing changed'), findsOneWidget);
    expect(
      t
          .widget<FilledButton>(
            find.widgetWithText(FilledButton, 'Review picked changes'),
          )
          .onPressed,
      isNull,
    );
    await t.tap(find.text('Cancel'));
    await t.pumpAndSettle();
    expect(result, isNull);
  });
  testWidgets(
    'All five tools are discoverable in Manufacturing without changing the shared view',
    (t) async {
      final m = ExpandedModel();
      addTearDown(m.dispose);
      m.state['manufacturing']['graphicalPicking'] = false;
      m.state['manufacturing']['operations'] = <Json>[
        {
          'id': 'profile',
          'name': 'Profile',
          'kind': 'router_offset',
          'entity_ids': ['entity_123456'],
          'profile_retention': <String, dynamic>{},
        },
        {
          'id': 'fill',
          'name': 'Pocket',
          'kind': 'island_fill',
          'entity_ids': ['entity_123456'],
          'profile_retention': <String, dynamic>{},
        },
      ];
      t.view.physicalSize = const Size(1600, 1000);
      t.view.devicePixelRatio = 1;
      addTearDown(t.view.resetPhysicalSize);
      addTearDown(t.view.resetDevicePixelRatio);
      final shared = DesignViewport()
        ..scale = 27
        ..translation = const Offset(123, 456);
      addTearDown(shared.dispose);
      await t.pumpWidget(
        MaterialApp(
          home: ManufacturingWorkspace(
            model: m,
            view: shared,
            onSave: () async => true,
          ),
        ),
      );
      await t.pumpAndSettle();
      await t.tap(find.text('Pick'));
      await t.pumpAndSettle();
      for (final name in pickingNames.values) {
        expect(find.textContaining(name), findsOneWidget);
      }
      await t.tap(find.textContaining('Pick profile start'));
      await t.pumpAndSettle();
      expect(find.textContaining('earlier backend'), findsWidgets);
      expect(shared.scale, 27);
      expect(shared.translation, const Offset(123, 456));
    },
  );
  testWidgets(
    'Final release requires all bodies and returns the graphical order without editing the document',
    (t) async {
      final m = await mount(t, 'release', compact: true);
      final before = jsonEncode(m.state);
      await t.tap(find.text('Re-audit release order'));
      await t.pumpAndSettle();
      expect(find.textContaining('Pick every physical body'), findsOneWidget);
      await click(t, const Offset(9, 3));
      await click(t, const Offset(3, 3));
      await capture(t, 'final-release-compact');
      await t.tap(find.text('Re-audit release order'));
      await t.pumpAndSettle();
      expect(result!['ids'], ['other', 'entity_123456']);
      expect(result!['fingerprint'], 'current');
      expect(jsonEncode(m.state), before);
      expect(m.calls, isEmpty);
      expect(t.takeException(), isNull);
    },
  );
  testWidgets(
    'Final release stale drawing disables submission and cancel preserves selection',
    (t) async {
      final m = await mount(t, 'release');
      final selection = List.of(m.state['selected']);
      await click(t, const Offset(9, 3));
      m.state['revision'] = (m.state['revision'] as int) + 1;
      m.notifyListeners();
      await t.pumpAndSettle();
      expect(
        t
            .widget<FilledButton>(
              find.widgetWithText(FilledButton, 'Re-audit release order'),
            )
            .onPressed,
        isNull,
      );
      await t.tap(find.text('Cancel'));
      await t.pumpAndSettle();
      expect(result, isNull);
      expect(m.state['selected'], selection);
      expect(m.calls, isEmpty);
    },
  );
}
