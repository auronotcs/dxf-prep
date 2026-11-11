import 'dart:convert';
import 'dart:io';
import 'dart:ui' as ui;
import 'package:flutter/material.dart';
import 'package:flutter/rendering.dart';
import 'package:flutter/services.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:workbench_prototype/design/controller.dart';
import 'package:workbench_prototype/design/manufacturing_workspace.dart';
import 'package:workbench_prototype/design/keepout_painter.dart';
import 'manufacturing_expanded_test.dart' show ExpandedModel;

final zone = <String, dynamic>{
  'id': 'clamp',
  'name': 'Side clamp',
  'clearance_z': .5,
  'enabled': true,
  'points': [
    {'x': 12, 'y': 12},
    {'x': 14, 'y': 12},
    {'x': 14, 'y': 13},
    {'x': 12, 'y': 13},
  ],
};

void main() {
  setUpAll(() async {
    for (final family in ['Roboto', 'Segoe UI']) {
      await (FontLoader(family)..addFont(
            File(
              'C:/Windows/Fonts/segoeui.ttf',
            ).readAsBytes().then((v) => ByteData.sublistView(v)),
          ))
          .load();
    }
    await (FontLoader('MaterialIcons')..addFont(
          File(
            '../.flutter-sdk/bin/cache/artifacts/material_fonts/MaterialIcons-Regular.otf',
          ).readAsBytes().then((v) => ByteData.sublistView(v)),
        ))
        .load();
  });
  Future<void> capture(WidgetTester t, String name) async {
    final boundary = t.renderObject<RenderRepaintBoundary>(
      find.byKey(const ValueKey('keepout-image')),
    );
    await t.runAsync(() async {
      final image = await boundary.toImage();
      final bytes = await image.toByteData(format: ui.ImageByteFormat.png);
      final directory = Directory('build/ui-review')
        ..createSync(recursive: true);
      File(
        '${directory.path}/keepouts-$name.png',
      ).writeAsBytesSync(bytes!.buffer.asUint8List());
      image.dispose();
    });
  }

  Future<ExpandedModel> mount(
    WidgetTester t, {
    bool old = false,
    bool existing = false,
    Size size = const Size(1400, 1000),
  }) async {
    t.view.physicalSize = size;
    t.view.devicePixelRatio = 1;
    addTearDown(t.view.resetPhysicalSize);
    addTearDown(t.view.resetDevicePixelRatio);
    final m = ExpandedModel();
    addTearDown(m.dispose);
    m.state['manufacturing']['keepoutsEditable'] = !old;
    m.state['manufacturing']['keepouts'] = <Json>[if (existing) zone];
    m.response = {
      'status': 'management_ready',
      'action': 'keepouts',
      'token': 'review',
      'keepouts': [zone],
      'motions': [],
      'details': {
        'geometry_changes': [],
        'operation_order': [],
        'keepout_change': {
          'change': 'add',
          'after': zone,
          'before': null,
          'protection_removed': false,
        },
      },
    };
    await t.pumpWidget(
      RepaintBoundary(
        key: const ValueKey('keepout-image'),
        child: MaterialApp(
          home: ManufacturingWorkspace(model: m, onSave: () async => true),
        ),
      ),
    );
    await t.pumpAndSettle();
    return m;
  }

  Future<void> click(WidgetTester t, String text) async {
    final f = find.text(text);
    await t.ensureVisible(f);
    await t.pumpAndSettle();
    await t.tap(f);
    await t.pumpAndSettle();
  }

  // TextFormField exposes decoration on the descendant TextField.
  Finder input(String label) => find.byWidgetPredicate(
    (w) => w is TextField && w.decoration?.labelText == label,
  );

  testWidgets(
    'Rectangle validates numeric fields and prepares a private reviewed draft',
    (t) async {
      final m = await mount(t);
      final before = jsonEncode(m.state);
      await click(t, 'Keepouts');
      await click(t, 'Add rectangle');
      await t.enterText(input('Width'), '0');
      await click(t, 'Review keepout');
      expect(find.text('Must be greater than zero.'), findsOneWidget);
      expect(m.requestedCommand, isNull);
      await t.enterText(input('Width'), '2');
      await t.enterText(input('X'), '12');
      await t.enterText(input('Y'), '12');
      await click(t, 'Review keepout');
      await t.pump(const Duration(milliseconds: 400));
      await t.pumpAndSettle();
      expect(m.requestedCommand, 'manufacturing.management_prepare');
      expect(m.requested?['action'], 'keepouts');
      expect(m.requested?['params']['x'], 12);
      expect(jsonEncode(m.state), before);
      final apply = find.widgetWithText(FilledButton, 'Apply workflow draft');
      await t.scrollUntilVisible(
        apply,
        200,
        scrollable: find
            .descendant(
              of: find.byType(ListView).first,
              matching: find.byType(Scrollable),
            )
            .first,
      );
      expect(t.widget<FilledButton>(apply).onPressed, isNull);
      await capture(t, 'draft');
      expect(
        find.byWidgetPredicate(
          (w) =>
              w is CustomPaint &&
              w.painter is MotionPainter &&
              (w.painter as MotionPainter).keepouts.isNotEmpty,
        ),
        findsOneWidget,
      );
      expect(t.takeException(), isNull);
    },
  );

  testWidgets('Selected contour cancellation preserves state and selection', (
    t,
  ) async {
    final m = await mount(t);
    final before = jsonEncode(m.state);
    await click(t, 'Keepouts');
    await click(t, 'From selected contour');
    await click(t, 'Cancel');
    await t.pump(const Duration(milliseconds: 400));
    await t.pumpAndSettle();
    expect(m.requestedCommand, isNull);
    expect(jsonEncode(m.state), before);
    await click(t, 'Keepouts');
    await click(t, 'From selected contour');
    await click(t, 'Review keepout');
    await t.pump(const Duration(milliseconds: 400));
    await t.pumpAndSettle();
    expect(m.requested?['params']['shape'], 'selection');
    expect(m.requested?['params']['entity_id'], 'entity_123456');
  });

  testWidgets(
    'Keepout edit/move and removal are reviewed, with visible protection notice',
    (t) async {
      final m = await mount(t, existing: true);
      expect(
        find.byWidgetPredicate(
          (w) => w is CustomPaint && w.foregroundPainter is KeepoutPainter,
        ),
        findsOneWidget,
      );
      await click(t, 'Keepouts');
      await click(t, 'Edit / move');
      await t.enterText(input('Move X'), '3');
      await click(t, 'Review keepout');
      await t.pump(const Duration(milliseconds: 400));
      await t.pumpAndSettle();
      expect(m.requested?['params']['dx'], 3);
      expect(m.requested?['params']['id'], 'clamp');
      m.response!['details']['keepout_change'] = {
        'change': 'delete',
        'before': zone,
        'after': null,
        'protection_removed': true,
      };
      await click(t, 'Keepouts');
      await click(t, 'Delete');
      expect(m.requested?['params'], {'change': 'delete', 'id': 'clamp'});
      expect(
        find.textContaining('This removes keepout protection'),
        findsOneWidget,
      );
      expect(t.takeException(), isNull);
    },
  );

  testWidgets(
    'Old backend preserves document; compact manager scrolls without overflow',
    (t) async {
      final m = await mount(t, old: true, size: const Size(800, 600));
      final before = jsonEncode(m.state);
      await click(t, 'Keepouts');
      expect(find.textContaining('earlier backend'), findsOneWidget);
      await click(t, 'Close');
      expect(jsonEncode(m.state), before);
      expect(m.requestedCommand, isNull);
      m.state['manufacturing']['keepoutsEditable'] = true;
      m.state['manufacturing']['keepouts'] = [zone];
      m.notifyListeners();
      await t.pumpAndSettle();
      await click(t, 'Keepouts');
      await click(t, 'Add rectangle');
      expect(t.takeException(), isNull);
      await capture(t, 'compact-editor');
      await click(t, 'Cancel');
      await t.pump(const Duration(milliseconds: 400));
      await t.pumpAndSettle();
    },
  );
}
